/** Persist dashboard tab across normal reload; hard refresh resets to Overview. */

const TAB_KEY = "nav-client-tab"
const HARD_KEY = "nav-client-hard-refresh"

const VALID_TABS = new Set([
  "overview",
  "demo",
  "logs",
  "flows",
  "graph",
  "knowledge",
  "bio",
  "settings",
  "monitor",
])

export const rememberTab = (tab: string) => {
  if (!VALID_TABS.has(tab)) return
  try {
    sessionStorage.setItem(TAB_KEY, tab)
  } catch {
    /* private mode */
  }
}

/** Call once before React mounts — marks Ctrl/Cmd+Shift+R (or Shift+F5). */
export const installRefreshGuards = () => {
  if (typeof window === "undefined") return
  window.addEventListener(
    "keydown",
    (e) => {
      const isReload =
        e.key === "F5" ||
        ((e.key === "r" || e.key === "R") && (e.ctrlKey || e.metaKey))
      if (!isReload) return
      try {
        if (e.shiftKey) sessionStorage.setItem(HARD_KEY, "1")
        else sessionStorage.removeItem(HARD_KEY)
      } catch {
        /* ignore */
      }
    },
    true,
  )
}

export const initialTab = (): string => {
  try {
    if (sessionStorage.getItem(HARD_KEY) === "1") {
      sessionStorage.removeItem(HARD_KEY)
      sessionStorage.removeItem(TAB_KEY)
      return "overview"
    }
    const saved = sessionStorage.getItem(TAB_KEY) || ""
    if (VALID_TABS.has(saved)) return saved
  } catch {
    /* ignore */
  }
  return "overview"
}
