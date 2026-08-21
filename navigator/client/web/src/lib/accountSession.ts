/** Shared Client account (email + product) — Sidebar + boot. */

import { create } from "zustand";
import { api } from "./api";
import { loadUserPreferences } from "./onboarding";

type State = {
  email: string;
  productName: string;
  productId: string;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  reset: () => void;
};

function applyAccount(
  set: (partial: Partial<State> | ((s: State) => Partial<State>)) => void,
  data: { email?: string; product_name?: string; product_id?: string },
) {
  const email = String(data.email ?? "").trim();
  const productName = String(data.product_name ?? "").trim();
  const productId = String(data.product_id ?? "").trim();
  // Never clobber good values with empty (race / old server).
  set((s) => ({
    email: email || s.email,
    productName: productName || s.productName,
    productId: productId || s.productId,
    loading: false,
    error: null,
  }));
}

export const useAccountSession = create<State>((set) => ({
  email: "",
  productName: "",
  productId: "",
  loading: false,
  error: null,
  refresh: async () => {
    set({ loading: true, error: null });
    try {
      // Prefs first (includes email/product); then authoritative /account.
      const prefs = await loadUserPreferences();
      if (prefs.email || prefs.product_name || prefs.product_id) {
        applyAccount(set, prefs);
      }

      try {
        const me = await api.getAccount();
        applyAccount(set, me);
      } catch {
        /* /account may 404 on stale server — prefs already applied */
        set((s) => ({ ...s, loading: false }));
      }
    } catch (e) {
      // Still try /account alone if prefs failed.
      try {
        const me = await api.getAccount();
        applyAccount(set, me);
      } catch {
        set({
          loading: false,
          error: e instanceof Error ? e.message : String(e),
        });
      }
    }
  },
  reset: () =>
    set({
      email: "",
      productName: "",
      productId: "",
      loading: false,
      error: null,
    }),
}));
