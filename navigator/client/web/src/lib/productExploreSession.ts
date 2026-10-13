/** Global Product Explore job — survives tab switches across the Client dashboard. */

import { create } from "zustand";
import { api } from "./api";
import type { ChecklistTone } from "../components/StatusChecklist";
import { useProductData } from "./productData";

export type ProductExploreArtifact = {
  id: string;
  label: string;
  detail?: string;
  status: ChecklistTone;
};

export type ProductExploreStatus = {
  active: boolean;
  phase?: string;
  pages_seen?: number;
  max_pages?: number;
  progress_pct?: number;
  current_url?: string;
  current_title?: string;
  looking_at?: string;
  error?: string | null;
  done?: boolean;
  start_url?: string;
  artifacts?: ProductExploreArtifact[];
};

type State = {
  status: ProductExploreStatus;
  starting: boolean;
  stopping: boolean;
  /** True only after this browser session called start() — blocks stale done UI. */
  sessionStarted: boolean;
  bootstrapped: boolean;
  /** Outcome of the last run this session saw finish — survives ack() clearing status. */
  lastFinish: { phase: "done" | "error"; error?: string | null; at: number } | null;
  clearLastFinish: () => void;
  refresh: () => Promise<ProductExploreStatus>;
  start: (startUrl?: string) => Promise<ProductExploreStatus>;
  stop: () => Promise<ProductExploreStatus>;
  ack: () => Promise<ProductExploreStatus>;
  bootstrap: () => void;
};

let pollTimer: ReturnType<typeof setInterval> | null = null;

const IDLE: ProductExploreStatus = { active: false, artifacts: [] };

function clearPoll() {
  if (pollTimer != null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function mapStatus(raw: Awaited<ReturnType<typeof api.getProductExplore>>): ProductExploreStatus {
  return {
    active: !!raw.active,
    phase: raw.phase,
    pages_seen: raw.pages_seen,
    max_pages: raw.max_pages,
    progress_pct: raw.progress_pct,
    current_url: raw.current_url,
    current_title: raw.current_title,
    looking_at: raw.looking_at,
    error: raw.error,
    done: raw.done,
    start_url: raw.start_url,
    artifacts: (raw.artifacts ?? []).map((a) => ({
      id: a.id,
      label: a.label,
      detail: a.detail,
      status: a.status,
    })),
  };
}

function ensurePoll(get: () => State) {
  if (pollTimer != null) return;
  pollTimer = setInterval(() => {
    const s = get().status;
    if (!s.active) {
      clearPoll();
      return;
    }
    void get().refresh();
  }, 1000);
}

export const useProductExploreSession = create<State>((set, get) => ({
  status: { ...IDLE },
  starting: false,
  stopping: false,
  sessionStarted: false,
  bootstrapped: false,
  lastFinish: null,
  clearLastFinish: () => set({ lastFinish: null }),

  refresh: async () => {
    try {
      const raw = await api.getProductExplore();
      const status = mapStatus(raw);
      set({ status });
      if (!status.active && (status.phase === "done" || status.phase === "error")) {
        set({ lastFinish: finishOf(status) });
      }
      if (status.active) ensurePoll(get);
      else clearPoll();
      if (status.done || (!status.active && status.phase === "done")) {
        useProductData.getState().invalidate();
      }
      return status;
    } catch {
      return get().status;
    }
  },

  start: async (startUrl) => {
    set({ starting: true, sessionStarted: true, lastFinish: null });
    try {
      let url = (startUrl || "").trim();
      if (!url) {
        try {
          const [domain, login] = await Promise.all([
            api.getProductDomain(),
            api.getProductLogin(),
          ]);
          url = (login.login_url || domain.base_url || "").trim();
        } catch {
          url = "";
        }
      }
      const raw = await api.startProductExplore(url);
      const status = mapStatus(raw);
      set({ status, starting: false });
      ensurePoll(get);
      return status;
    } catch (e) {
      set({ starting: false });
      throw e;
    }
  },

  stop: async () => {
    set({ stopping: true });
    try {
      const raw = await api.stopProductExplore();
      const status = mapStatus(raw);
      set({ status, stopping: false, sessionStarted: false });
      clearPoll();
      useProductData.getState().invalidate();
      return status;
    } catch (e) {
      set({ stopping: false });
      throw e;
    }
  },

  ack: async () => {
    const prev = get().status;
    if (!prev.active && (prev.phase === "done" || prev.phase === "error")) {
      set({ lastFinish: finishOf(prev) });
    }
    try {
      const raw = await api.ackProductExplore();
      const status = mapStatus(raw);
      set({
        status: { ...IDLE, artifacts: status.artifacts ?? [] },
        sessionStarted: false,
      });
      clearPoll();
      useProductData.getState().invalidate();
      return get().status;
    } catch {
      set({ status: { ...IDLE }, sessionStarted: false });
      clearPoll();
      return get().status;
    }
  },

  bootstrap: () => {
    if (get().bootstrapped) return;
    set({ bootstrapped: true });
    void get().refresh().then((st) => {
      // Never treat leftover server done as a user-started run.
      if (st.active) ensurePoll(get);
      else if (st.phase === "done" || st.done) {
        void get().ack();
      }
    });
  },
}));

function finishOf(st: ProductExploreStatus): State["lastFinish"] {
  const failed = st.phase === "error" || !!st.error;
  return { phase: failed ? "error" : "done", error: st.error ?? null, at: Date.now() };
}

/** Client-facing label for a backend job phase. */
export function productExplorePhaseLabel(phase?: string): string {
  switch (phase) {
    case "starting":
      return "Starting…";
    case "signing_in":
      return "Signing in to your product…";
    case "crawling":
      return "Crawling product pages…";
    case "web_research":
      return "Gathering public product context…";
    case "writing_bio":
      return "Filling company bio…";
    case "updating_knowledge":
      return "Updating knowledge…";
    case "done":
      return "Done";
    case "error":
      return "Explore failed";
    default:
      return "Waiting to start…";
  }
}

export function productExploreIsLive(status: ProductExploreStatus): boolean {
  return !!status.active;
}

export function productExplorePct(status: ProductExploreStatus): number {
  const raw = status.progress_pct;
  if (typeof raw === "number" && !Number.isNaN(raw)) {
    return Math.min(100, Math.max(0, Math.round(raw)));
  }
  const seen = status.pages_seen ?? 0;
  const max = Math.max(1, status.max_pages ?? 25);
  return Math.min(95, Math.round((seen / max) * 70));
}
