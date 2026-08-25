/** Shared live-demo session — survives panel unmount / tab switches. */

import { create } from "zustand";
import { api, type Demo, type StartDemoBody } from "./api";
import { errText } from "../store";

const ACTIVE = new Set(["starting", "running"]);

function pickActive(list: Demo[]): Demo | null {
  const live = list.filter((d) => ACTIVE.has(d.status));
  if (!live.length) return null;
  // Newest first if timestamps absent — list order from runner is fine.
  return live[0];
}

type DemoSession = {
  demo: Demo | null;
  starting: boolean;
  ending: boolean;
  hydrated: boolean;
  setDemo: (d: Demo | null) => void;
  hydrate: () => Promise<void>;
  refreshActive: () => Promise<void>;
  start: (body: StartDemoBody) => Promise<Demo>;
  end: (demoId?: string) => Promise<Demo | null>;
};

export const useDemoSession = create<DemoSession>((set, get) => ({
  demo: null,
  starting: false,
  ending: false,
  hydrated: false,

  setDemo: (demo) => set({ demo }),

  hydrate: async () => {
    try {
      const list = await api.listDemos();
      const active = pickActive(list);
      if (active) {
        try {
          const fresh = await api.getDemo(active.demo_id);
          set({ demo: fresh, hydrated: true });
        } catch {
          set({ demo: active, hydrated: true });
        }
        return;
      }
      const cur = get().demo;
      if (cur && ACTIVE.has(cur.status)) {
        set({ demo: { ...cur, status: "finished", bot_in_meeting: false }, hydrated: true });
        return;
      }
      if (cur?.demo_id) {
        try {
          const fresh = await api.getDemo(cur.demo_id);
          set({ demo: fresh, hydrated: true });
        } catch {
          set({ demo: cur, hydrated: true });
        }
        return;
      }
      set({ hydrated: true });
    } catch {
      set({ hydrated: true });
    }
  },

  refreshActive: async () => {
    const cur = get().demo;
    if (!cur || !ACTIVE.has(cur.status)) {
      try {
        const list = await api.listDemos();
        const active = pickActive(list);
        if (active) {
          try {
            const fresh = await api.getDemo(active.demo_id);
            set({ demo: fresh });
          } catch {
            set({ demo: active });
          }
          return;
        }
        if (cur?.demo_id) {
          try {
            const fresh = await api.getDemo(cur.demo_id);
            set({ demo: fresh });
          } catch {
            if (cur && ACTIVE.has(cur.status)) {
              set({ demo: { ...cur, status: "finished", bot_in_meeting: false } });
            }
          }
        }
      } catch {
        /* ignore poll errors */
      }
      return;
    }
    try {
      const fresh = await api.getDemo(cur.demo_id);
      set({ demo: fresh });
    } catch (e) {
      // 404 → ended elsewhere
      const msg = errText(e);
      if (msg.toLowerCase().includes("404") || msg.toLowerCase().includes("no such")) {
        set({ demo: { ...cur, status: "finished", bot_in_meeting: false } });
      }
    }
  },

  start: async (body) => {
    if (get().starting) throw new Error("Already starting a demo");
    const cur = get().demo;
    if (cur && ACTIVE.has(cur.status)) {
      throw new Error("A demo is already running — end it first");
    }
    set({ starting: true });
    try {
      const d = await api.startDemo(body);
      set({ demo: d, starting: false });
      return d;
    } catch (e) {
      set({ starting: false });
      throw e;
    }
  },

  end: async (demoId) => {
    const id = demoId || get().demo?.demo_id;
    if (!id) return null;
    if (get().ending) return get().demo;
    set({ ending: true });
    try {
      const d = await api.endDemo(id);
      set({ demo: d, ending: false });
      return d;
    } catch (e) {
      const msg = errText(e).toLowerCase();
      // Already gone from runner — treat as ended so Logs/UI unstick.
      if (msg.includes("no such demo") || msg.includes("404")) {
        const cur = get().demo;
        const finished = cur
          ? {
              ...cur,
              status: "finished" as const,
              demo_id: id,
              bot_in_meeting: false,
            }
          : null;
        set({ demo: finished, ending: false });
        return finished;
      }
      set({ ending: false });
      throw e;
    }
  },
}));

export function demoIsLive(demo: Demo | null | undefined): boolean {
  return !!demo && ACTIVE.has(demo.status);
}
