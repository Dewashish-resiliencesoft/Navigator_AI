/** Shared Client product config — playlist + invalidate epoch for all panels. */

import { create } from "zustand";
import { api, type Flow } from "./api";

type ProductData = {
  /** Bumps after any server-side product config mutation. Panels refetch on change. */
  epoch: number;
  playlist: Flow[];
  setPlaylist: (playlist: Flow[]) => void;
  /** Apply server playlist and notify every panel to reload. */
  applyPlaylist: (playlist: Flow[]) => void;
  refreshPlaylist: () => Promise<Flow[]>;
  /** Notify panels to refetch (site graph, knowledge, login, …). */
  invalidate: () => void;
  reset: () => void;
};

export const useProductData = create<ProductData>((set) => ({
  epoch: 0,
  playlist: [],
  setPlaylist: (playlist) => set({ playlist }),
  applyPlaylist: (playlist) =>
    set((s) => ({ playlist, epoch: s.epoch + 1 })),
  refreshPlaylist: async () => {
    const d = await api.getFlows();
    const playlist = d.playlist ?? [];
    set((s) => ({ playlist, epoch: s.epoch + 1 }));
    return playlist;
  },
  invalidate: () => set((s) => ({ epoch: s.epoch + 1 })),
  reset: () => set({ epoch: 0, playlist: [] }),
}));
