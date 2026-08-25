/** Shared demo readiness — Overview + Live Demo stay on one source of truth. */

import { create } from "zustand";
import { api, type DemoReadiness } from "./api";
import { useProductData } from "./productData";

type State = {
  readiness: DemoReadiness | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  bootstrap: () => void;
  reset: () => void;
};

let poll: ReturnType<typeof setInterval> | null = null;
let unsubEpoch: (() => void) | null = null;
let bootstrapped = false;

export const useDemoReadinessSession = create<State>((set, get) => ({
  readiness: null,
  loading: false,
  error: null,
  refresh: async () => {
    set({ loading: true });
    try {
      // Same origin Live Demo uses — draft site graph / test-demo gate.
      const readiness = await api.getDemoReadiness("dashboard_test");
      set({ readiness, error: null, loading: false });
    } catch (e) {
      set({
        error: e instanceof Error ? e.message : String(e),
        loading: false,
      });
    }
  },
  bootstrap: () => {
    if (bootstrapped) return;
    bootstrapped = true;
    void get().refresh();
    // Server readiness cache is ~12s; poll a bit slower than that.
    poll = setInterval(() => void get().refresh(), 15_000);
    unsubEpoch = useProductData.subscribe((s, prev) => {
      if (s.epoch !== prev.epoch) void get().refresh();
    });
  },
  reset: () => {
    bootstrapped = false;
    if (poll != null) {
      clearInterval(poll);
      poll = null;
    }
    unsubEpoch?.();
    unsubEpoch = null;
    set({ readiness: null, loading: false, error: null });
  },
}));
