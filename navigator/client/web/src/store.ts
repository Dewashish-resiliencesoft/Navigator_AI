import { create } from "zustand";
import { coachForCheck, type CoachGuide } from "./lib/coachTargets";

type Toast = { kind: "ok" | "err"; text: string } | null;

type State = {
  tab: string;
  toast: Toast;
  logsSessionId: string | null;
  coach: CoachGuide | null;
  setTab: (t: string) => void;
  setLogsSessionId: (id: string | null) => void;
  startCoach: (checkId: string) => void;
  clearCoach: () => void;
  ok: (text: string) => void;
  err: (text: string) => void;
  clear: () => void;
};

export const useUi = create<State>((set) => ({
  tab: "overview",
  toast: null,
  logsSessionId: null,
  coach: null,
  setTab: (tab) => set({ tab }),
  setLogsSessionId: (logsSessionId) => set({ logsSessionId }),
  startCoach: (checkId) => {
    const guide = coachForCheck(checkId);
    if (!guide) return;
    set({ tab: guide.tab, coach: guide });
  },
  clearCoach: () => set({ coach: null }),
  ok: (text) => set({ toast: { kind: "ok", text } }),
  err: (text) => set({ toast: { kind: "err", text } }),
  clear: () => set({ toast: null }),
}));

export const errText = (e: unknown) => {
  const raw = e instanceof Error ? e.message : String(e);
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (Array.isArray(parsed)) {
      return parsed
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            const loc = Array.isArray((item as { loc?: unknown }).loc)
              ? (item as { loc: unknown[] }).loc.join(".")
              : "";
            const msg = String((item as { msg: unknown }).msg);
            return loc ? `${loc}: ${msg}` : msg;
          }
          return JSON.stringify(item);
        })
        .join("; ");
    }
  } catch {
    /* not JSON validation detail */
  }
  return raw;
};
