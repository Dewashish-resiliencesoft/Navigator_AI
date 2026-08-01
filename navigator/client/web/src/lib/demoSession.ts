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
      set({ demo: active ?? get().demo, hydrated: true });
      if (active) {
        try {
          const fresh = await api.getDemo(active.demo_id);
          set({ demo: fresh });
        } catch {
          set({ demo: active });
        }
      }
    } catch {
      set({ hydrated: true });
    }
  },

  refreshActive: async () => {
    const cur = get().demo;
    if (!cur || !ACTIVE.has(cur.status)) {
      // Still scan list in case another tab / process started one.
      try {
        const list = await api.listDemos();
        const active = pickActive(list);
        if (active && active.demo_id !== cur?.demo_id) {
          set({ demo: active });
        } else if (cur && !ACTIVE.has(cur.status)) {
          // keep finished demo visible until user starts again
        } else if (!active && cur && ACTIVE.has(cur.status)) {
          // lost from runner — mark finished
          set({ demo: { ...cur, status: "finished" } });
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
        set({ demo: { ...cur, status: "finished" } });
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
      set({ ending: false });
      throw e;
    }
  },
}));

export function demoIsLive(demo: Demo | null | undefined): boolean {
  return !!demo && ACTIVE.has(demo.status);
}
