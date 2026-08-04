/** Shared auto-explore session — survives Flows panel unmount / tab switches. */

import { create } from "zustand";
import {
  api,
  exploreSocketUrl,
  type ExploreEvent,
  type ExploreQuestion,
  type ExploreStatus,
} from "./api";
import { errText } from "../store";
import { useProductData } from "./productData";

let socket: WebSocket | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let tickTimer: ReturnType<typeof setInterval> | null = null;
let onFlowDrafted: (() => void) | null = null;

function isTerminal(phase?: string): boolean {
  return phase === "done" || phase === "failed" || phase === "stopped";
}

function clearTimers() {
  if (pollTimer != null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (tickTimer != null) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
}

type ExploreFrame = { mime: string; data: string };

type ExploreSession = {
  status: ExploreStatus;
  events: ExploreEvent[];
  question: ExploreQuestion | null;
  answer: string;
  baseUrl: string;
  /** True after Client edits the Flows Product URL field — skip auto-sync. */
  baseUrlTouched: boolean;
  saveMode: "new" | "update";
  targetFlowId: string;
  targetFlowName: string;
  elapsedLocal: number;
  showMeter: boolean;
  /** Latest server Chromium viewport (Watch bot). */
  latestFrame: ExploreFrame | null;
  setBaseUrl: (v: string) => void;
  setAnswer: (v: string) => void;
  setSaveMode: (v: "new" | "update") => void;
  setTargetFlowId: (v: string) => void;
  setTargetFlowName: (v: string) => void;
  setOnFlowDrafted: (cb: (() => void) | null) => void;
  /** Prefill baseUrl from Product Login URL, else Product Domain (if untouched). */
  syncProductUrl: () => Promise<string>;
  hydrate: () => Promise<ExploreStatus | null>;
  refresh: () => Promise<ExploreStatus | null>;
  connect: () => Promise<void>;
  start: (opts?: { targetFlowName?: string }) => Promise<void>;
  stop: () => Promise<void>;
  reply: (skip: boolean) => Promise<void>;
  /** Hide post-run summary / live log leftovers so Flows looks idle again. */
  dismissResult: () => void;
  pullFrame: () => Promise<ExploreFrame | null>;
};

function applyLiveTimers(get: () => ExploreSession, set: (p: Partial<ExploreSession>) => void) {
  clearTimers();
  const live = () => {
    const s = get();
    return Boolean(s.status.active) || (s.showMeter && !isTerminal(s.status.phase));
  };
  if (!live()) return;
  pollTimer = setInterval(() => {
    void get().refresh();
  }, 1500);
  tickTimer = setInterval(() => {
    if (!live()) return;
    set({ elapsedLocal: get().elapsedLocal + 1 });
  }, 1000);
}

export const useExploreSession = create<ExploreSession>((set, get) => ({
  status: { active: false },
  events: [],
  question: null,
  answer: "",
  baseUrl: "",
  baseUrlTouched: false,
  saveMode: "new",
  targetFlowId: "",
  targetFlowName: "",
  elapsedLocal: 0,
  showMeter: false,
  latestFrame: null,

  setBaseUrl: (baseUrl) => set({ baseUrl, baseUrlTouched: true }),
  setAnswer: (answer) => set({ answer }),
  setSaveMode: (saveMode) => set({ saveMode }),
  setTargetFlowId: (targetFlowId) => set({ targetFlowId }),
  setTargetFlowName: (targetFlowName) => set({ targetFlowName }),
  setOnFlowDrafted: (cb) => {
    onFlowDrafted = cb;
  },

  syncProductUrl: async () => {
    if (get().baseUrlTouched) return get().baseUrl;
    try {
      const login = await api.getProductLogin();
      const loginUrl = (login.login_url || "").trim();
      if (loginUrl) {
        set({ baseUrl: loginUrl });
        return loginUrl;
      }
    } catch {
      /* fall through to domain */
    }
    try {
      const d = await api.getProductDomain();
      const domain = (d.base_url || "").trim();
      if (domain && !d.placeholder) {
        set({ baseUrl: domain });
        return domain;
      }
    } catch {
      /* keep empty */
    }
    return get().baseUrl;
  },

  refresh: async () => {
    try {
      const s = await api.exploreStatus();
      set((prev) => ({
        status: { ...prev.status, ...s },
        question: s.pending_question ?? null,
        showMeter: prev.showMeter || Boolean(s.active) || (s.steps ?? 0) > 0,
        elapsedLocal:
          typeof s.elapsed_s === "number" ? s.elapsed_s : prev.elapsedLocal,
        events:
          prev.events.length === 0 && (s.recent_events?.length ?? 0) > 0
            ? (s.recent_events ?? [])
            : prev.events,
      }));
      applyLiveTimers(get, set);
      return s;
    } catch {
      return null;
    }
  },

  hydrate: async () => {
    const s = await get().refresh();
    if (s?.active) await get().connect();
    await get().syncProductUrl();
    return s;
  },

  connect: async () => {
    // One live socket — remount/hydrate must not replay the log 3×.
    if (
      socket &&
      (socket.readyState === WebSocket.OPEN ||
        socket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    try {
      socket?.close();
    } catch {
      /* ignore */
    }
    // Server replays the full buffer after accept — start clean.
    set({ events: [] });
    const ws = new WebSocket(await exploreSocketUrl());
    socket = ws;
    ws.onmessage = (m) => {
      const event: ExploreEvent = JSON.parse(m.data);
      if (event.type === "status") {
        const next = event as unknown as ExploreStatus;
        set((prev) => ({
          status: {
            ...prev.status,
            ...next,
            has_credentials: prev.status.has_credentials ?? next.has_credentials,
          },
          showMeter: prev.showMeter || Boolean(next.active),
          elapsedLocal:
            typeof next.elapsed_s === "number" ? next.elapsed_s : prev.elapsedLocal,
        }));
        applyLiveTimers(get, set);
        return;
      }
      if (event.type === "state") {
        set((prev) => ({
          status: {
            ...prev.status,
            active: true,
            phase: typeof event.phase === "string" ? event.phase : prev.status.phase,
            visited:
              typeof event.visited === "number" ? event.visited : prev.status.visited,
            steps: typeof event.steps === "number" ? event.steps : prev.status.steps,
            elapsed_s:
              typeof event.elapsed_s === "number"
                ? event.elapsed_s
                : prev.status.elapsed_s,
            progress_pct:
              typeof event.progress_pct === "number"
                ? event.progress_pct
                : prev.status.progress_pct,
          },
          showMeter: true,
          elapsedLocal:
            typeof event.elapsed_s === "number" ? event.elapsed_s : prev.elapsedLocal,
        }));
        applyLiveTimers(get, set);
        return;
      }
      if (event.type === "frame" && typeof event.data === "string" && event.data) {
        set({
          latestFrame: {
            mime: typeof event.mime === "string" ? event.mime : "image/jpeg",
            data: event.data,
          },
        });
        return;
      }
      set((prev) => ({ events: [...prev.events, event] }));
      if (event.type === "question") {
        set({
          question: {
            qid: String(event.qid),
            alias: String(event.alias),
            prompt: String(event.prompt),
            context: (event.context ?? {}) as Record<string, string>,
          },
          answer: "",
        });
      }
      if (event.type === "error") {
        const msg = String(event.msg ?? "Exploration error").trim();
        set((prev) => ({
          status: {
            ...prev.status,
            error: msg || prev.status.error,
            active: false,
            phase: prev.status.phase === "failed" ? "failed" : prev.status.phase,
          },
        }));
      }
      if (event.type === "done") {
        set((prev) => ({
          question: null,
          status: {
            ...prev.status,
            active: false,
            phase: typeof event.phase === "string" ? event.phase : "done",
            steps: typeof event.steps === "number" ? event.steps : prev.status.steps,
            flow_id:
              typeof event.flow_id === "string" && event.flow_id
                ? event.flow_id
                : prev.status.flow_id,
            revision:
              typeof event.revision === "number"
                ? event.revision
                : prev.status.revision,
            progress_pct: 100,
          },
        }));
        clearTimers();
        void get().refresh();
        onFlowDrafted?.();
        void useProductData.getState().refreshPlaylist();
      }
    };
    ws.onclose = () => {
      if (socket === ws) socket = null;
    };
  },

  start: async (opts) => {
    set({
      events: [],
      elapsedLocal: 0,
      showMeter: true,
      question: null,
      answer: "",
      latestFrame: null,
    });
    const mode = get().saveMode;
    const target = get().targetFlowId.trim();
    if (mode === "update" && !target) {
      throw new Error("Pick a flow to update, or switch to Create new flow.");
    }
    try {
      // Force a fresh WS for the new job (skip-if-open would reuse a dead log).
      try {
        socket?.close();
      } catch {
        /* ignore */
      }
      socket = null;
      if (mode === "update") {
        set({ targetFlowName: opts?.targetFlowName?.trim() || target });
      } else {
        set({ targetFlowName: "" });
      }
      const s = await api.exploreStart({
        base_url: get().baseUrl.trim() || null,
        save_mode: mode,
        target_flow_id: mode === "update" ? target : null,
        target_flow_name:
          mode === "update" ? (opts?.targetFlowName || null) : null,
      });
      set((prev) => ({
        status: { ...prev.status, ...s, active: true },
        showMeter: true,
      }));
      applyLiveTimers(get, set);
      await get().connect();
    } catch (e) {
      set({ showMeter: false, status: { active: false } });
      clearTimers();
      throw e;
    }
  },

  stop: async () => {
    // Optimistic: meter/buttons flip off before the explorer thread wakes.
    set((prev) => ({
      status: { ...prev.status, active: false, phase: "stopped" },
      question: null,
    }));
    clearTimers();
    try {
      const s = await api.exploreStop();
      set((prev) => ({
        status: {
          ...prev.status,
          ...s,
          active: false,
          phase: s.phase || "stopped",
        },
      }));
    } catch (e) {
      const fresh = await get().refresh();
      if (fresh?.active) throw e;
    }
    // Persist still runs after stop — poll until flow_id lands or timeout.
    for (let i = 0; i < 30; i++) {
      await new Promise((r) => setTimeout(r, 400));
      const fresh = await get().refresh();
      if (!fresh) break;
      if (fresh.flow_id || fresh.phase === "failed" || fresh.phase === "done") break;
      // No steps → nothing to draft
      if ((fresh.steps ?? 0) === 0 && isTerminal(fresh.phase)) break;
    }
  },

  reply: async (skip: boolean) => {
    const q = get().question;
    if (!q) return;
    await api.exploreAnswer(q.qid, get().answer, skip);
    set({ question: null, answer: "" });
  },

  dismissResult: () => {
    clearTimers();
    try {
      socket?.close();
    } catch {
      /* ignore */
    }
    socket = null;
    set({
      showMeter: false,
      events: [],
      question: null,
      answer: "",
      elapsedLocal: 0,
      latestFrame: null,
      status: { active: false, phase: "idle" },
    });
  },

  pullFrame: async () => {
    try {
      const frame = await api.exploreFrame();
      set({ latestFrame: frame });
      return frame;
    } catch {
      return get().latestFrame;
    }
  },
}));

export function exploreIsTerminal(phase?: string): boolean {
  return isTerminal(phase);
}

export function exploreIsLive(s: {
  status: ExploreStatus;
  showMeter: boolean;
}): boolean {
  return Boolean(s.status.active) || (s.showMeter && !isTerminal(s.status.phase));
}

export function formatExploreElapsed(totalSeconds: number): string {
  const sec = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const r = sec % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
  return `${m}:${String(r).padStart(2, "0")}`;
}

/** Soft error helper for callers that toast. */
export { errText as exploreErrText };
