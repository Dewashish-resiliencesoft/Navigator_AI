import { create } from "zustand";

type Toast = { kind: "ok" | "err"; text: string } | null;

type State = {
  tab: string;
  toast: Toast;
  setTab: (t: string) => void;
  ok: (text: string) => void;
  err: (text: string) => void;
  clear: () => void;
};

export const useUi = create<State>((set) => ({
  tab: "overview",
  toast: null,
  setTab: (tab) => set({ tab }),
  ok: (text) => set({ toast: { kind: "ok", text } }),
  err: (text) => set({ toast: { kind: "err", text } }),
  clear: () => set({ toast: null }),
}));

export const errText = (e: unknown) =>
  e instanceof Error ? e.message : String(e);
