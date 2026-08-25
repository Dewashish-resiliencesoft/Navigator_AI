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
import {
  DISPLAY_TICK_MS,
  syncElapsedAnchor,
  useElapsedSeconds,
} from "./elapsed";

let socket: WebSocket | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let onFlowDrafted: (() => void) | null = null;

function isTerminal(phase?: string): boolean {
  return phase === "done" || phase === "failed" || phase === "stopped" || phase === "idle";
}

/** Explore stopped but server still drafting the flow into the site graph. */
export function exploreIsPersisting(status: ExploreStatus): boolean {
  if (status.active) return false;
  if (status.flow_id) return false;
  if ((status.steps ?? 0) === 0) return false;
  const phase = status.phase ?? "idle";
  return phase === "stopped" || phase === "drafting" || phase === "saving";
}

export function exploreDraftProgressPct(status: ExploreStatus): number {
  const raw = status.progress_pct ?? 0;
  if (status.flow_id) return 100;
  if (exploreIsPersisting(status)) return Math.max(raw, 92);
  return raw;
}

async function waitForFlowDraft(
  refresh: () => Promise<ExploreStatus | null>,
  maxMs = 120_000,
): Promise<ExploreStatus | null> {
  const deadline = Date.now() + maxMs;
  let last: ExploreStatus | null = null;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 400));
    last = await refresh();
    if (!last) break;
    if (last.flow_id || last.phase === "failed" || last.phase === "done") break;
    if ((last.steps ?? 0) === 0 && isTerminal(last.phase)) break;
  }
  return last;
}

function clearTimers() {
  if (pollTimer != null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function loadScope(key: string): string[] {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String).filter(Boolean) : [];
  } catch {
    return [];
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
  newFlowName: string;
  focusHint: string;
  includePaths: string[];
  excludePaths: string[];
  excludeLabels: string[];
  /** Wall-clock anchor for elapsed display — not overwritten every poll. */
  elapsedAnchorMs: number | null;
  showMeter: boolean;
  /** Latest server Chromium viewport (Watch bot). */
  latestFrame: ExploreFrame | null;
  setBaseUrl: (v: string) => void;
  setAnswer: (v: string) => void;
  setSaveMode: (v: "new" | "update") => void;
  setTargetFlowId: (v: string) => void;
  setTargetFlowName: (v: string) => void;
  setNewFlowName: (v: string) => void;
  setFocusHint: (v: string) => void;
  setIncludePaths: (v: string[]) => void;
  setExcludePaths: (v: string[]) => void;
  setExcludeLabels: (v: string[]) => void;
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

function applyLiveTimers(get: () => ExploreSession) {
  clearTimers();
  const live = () => {
    const s = get();
    return (
      Boolean(s.status.active) ||
      exploreIsPersisting(s.status) ||
      (s.showMeter && !isTerminal(s.status.phase))
    );
  };
  if (!live()) return;
  pollTimer = setInterval(() => {
    void get().refresh();
  }, DISPLAY_TICK_MS * 2);
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
  newFlowName: "",
  focusHint: "",
  includePaths: loadScope("nav-explore-include-paths"),
  excludePaths: loadScope("nav-explore-exclude-paths"),
  excludeLabels: loadScope("nav-explore-exclude-labels"),
  elapsedAnchorMs: null,
  showMeter: false,
  latestFrame: null,

  setBaseUrl: (baseUrl) => set({ baseUrl, baseUrlTouched: true }),
  setAnswer: (answer) => set({ answer }),
  setSaveMode: (saveMode) => set({ saveMode }),
  setTargetFlowId: (targetFlowId) => set({ targetFlowId }),
  setTargetFlowName: (targetFlowName) => set({ targetFlowName }),
  setNewFlowName: (newFlowName) => set({ newFlowName }),
  setFocusHint: (focusHint) => set({ focusHint }),
  setIncludePaths: (includePaths) => set({ includePaths }),
  setExcludePaths: (excludePaths) => set({ excludePaths }),
  setExcludeLabels: (excludeLabels) => set({ excludeLabels }),
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
      set((prev) => {
        const merged = { ...prev.status, ...s };
        const persisting = exploreIsPersisting(merged);
        return {
          status: merged,
          question: s.pending_question ?? null,
          showMeter: Boolean(s.active) || persisting,
          elapsedAnchorMs: syncElapsedAnchor(
            prev.elapsedAnchorMs,
            s.elapsed_s,
          ),
          events:
            prev.events.length === 0 && (s.recent_events?.length ?? 0) > 0
              ? (s.recent_events ?? [])
              : prev.events,
        };
      });
      applyLiveTimers(get);
      return s;
    } catch {
      return null;
    }
  },

  hydrate: async () => {
    const s = await get().refresh();
    if (s?.active) {
      await get().connect();
    } else if (s && isTerminal(s.phase)) {
      if (exploreIsPersisting(s)) {
        void waitForFlowDraft(() => get().refresh()).then((final) => {
          if (final?.flow_id) {
            onFlowDrafted?.();
            void useProductData.getState().refreshPlaylist();
          }
          set((prev) => ({
            showMeter: final ? exploreIsPersisting({ ...prev.status, ...final }) : false,
          }));
        });
      } else {
        set({
          showMeter: false,
          events: [],
          question: null,
          elapsedAnchorMs: null,
          latestFrame: null,
          status: { active: false, phase: "idle" },
        });
      }
    }
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
        set((prev) => {
          const merged = { ...prev.status, ...next };
          return {
            status: {
              ...merged,
              has_credentials: prev.status.has_credentials ?? next.has_credentials,
            },
            showMeter:
              prev.showMeter ||
              Boolean(next.active) ||
              exploreIsPersisting(merged),
            elapsedAnchorMs: syncElapsedAnchor(
              prev.elapsedAnchorMs,
              next.elapsed_s,
            ),
          };
        });
        applyLiveTimers(get);
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
          elapsedAnchorMs: syncElapsedAnchor(
            prev.elapsedAnchorMs,
            typeof event.elapsed_s === "number" ? event.elapsed_s : undefined,
          ),
        }));
        applyLiveTimers(get);
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
        void useProductData.getState().refreshPlaylist().then(() => {
          set({ showMeter: false, events: [] });
        });
      }
    };
    ws.onclose = () => {
      if (socket === ws) socket = null;
    };
  },

  start: async (opts) => {
    set({
      events: [],
      elapsedAnchorMs: Date.now(),
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
        new_flow_name:
          mode === "new" ? get().newFlowName.trim() || null : null,
        focus_hint: get().focusHint.trim() || null,
        include_paths: get().includePaths,
        exclude_paths: get().excludePaths,
        exclude_labels: get().excludeLabels,
      });
      set((prev) => ({
        status: { ...prev.status, ...s, active: true },
        showMeter: true,
      }));
      applyLiveTimers(get);
      await get().connect();
    } catch (e) {
      set({ showMeter: false, status: { active: false } });
      clearTimers();
      throw e;
    }
  },

  stop: async () => {
    set((prev) => ({
      status: { ...prev.status, active: false, phase: "stopped" },
      question: null,
      showMeter: true,
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
        showMeter: true,
      }));
    } catch (e) {
      const fresh = await get().refresh();
      if (fresh?.active) throw e;
    }
    const final = await waitForFlowDraft(() => get().refresh());
    if (final?.flow_id) {
      onFlowDrafted?.();
      await useProductData.getState().refreshPlaylist();
    }
    set((prev) => ({
      showMeter: final
        ? exploreIsPersisting({ ...prev.status, ...final })
        : false,
      events: final?.flow_id ? [] : prev.events,
    }));
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
      elapsedAnchorMs: null,
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
  return (
    Boolean(s.status.active) ||
    exploreIsPersisting(s.status) ||
    (s.showMeter && !isTerminal(s.status.phase))
  );
}

export function useExploreElapsed(): number {
  const anchor = useExploreSession((s) => s.elapsedAnchorMs);
  const status = useExploreSession((s) => s.status);
  const showMeter = useExploreSession((s) => s.showMeter);
  const ticking =
    Boolean(status.active) ||
    exploreIsPersisting(status) ||
    (showMeter && !isTerminal(status.phase));
  return useElapsedSeconds(anchor, ticking);
}

export { formatElapsedClock as formatExploreElapsed } from "./elapsed";

/** Soft error helper for callers that toast. */
export { errText as exploreErrText };
