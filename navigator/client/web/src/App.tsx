import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AlertCircle, CheckCircle2, LogOut, Moon, PhoneOff, PlayCircle, Sun, X } from "lucide-react";
import { MobileTabs, Sidebar, TABS } from "./components/Sidebar";
import { Overview } from "./panels/Overview";
import { LiveDemo } from "./panels/LiveDemo";
import { Logs } from "./panels/Logs";
import { Flows } from "./panels/Flows";
import { Bio, Knowledge, SiteGraph } from "./panels/Editors";
import { soft } from "./lib/motion";
import { errText, useUi } from "./store";
import { api } from "./lib/api";
import { demoIsLive, useDemoSession } from "./lib/demoSession";
import { AuthScreen } from "./panels/AuthScreen";
import { Button, StatusPill } from "./components/ui";

const PANELS: Record<string, () => React.ReactElement> = {
  overview: Overview,
  demo: LiveDemo,
  logs: Logs,
  flows: Flows,
  graph: SiteGraph,
  knowledge: Knowledge,
  bio: Bio,
};

function Toast() {
  const { toast, clear } = useUi();
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(clear, 4000);
    return () => clearTimeout(t);
  }, [toast, clear]);

  return (
    <AnimatePresence>
      {toast && (
        <motion.div
          key={toast.text + toast.kind}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 6 }}
          transition={soft}
          drag="y"
          dragConstraints={{ top: 0, bottom: 50 }}
          dragElastic={0.2}
          onDragEnd={(_e, info) => {
            if (info.offset.y > 20 || info.velocity.y > 100) clear();
          }}
          className="fixed bottom-5 left-1/2 z-50 flex max-w-[92vw] -translate-x-1/2 items-center gap-2.5 rounded-xl border px-4 py-2.5 backdrop-blur-xl cursor-grab active:cursor-grabbing"
          style={{
            borderColor: "var(--line)",
            background: "color-mix(in oklch, var(--panel) 82%, transparent)",
          }}
        >
          {toast.kind === "ok" ? (
            <CheckCircle2 size={15} className="shrink-0 text-emerald-500" />
          ) : (
            <AlertCircle size={15} className="shrink-0 text-red-500" />
          )}
          <span className="text-[0.81rem] leading-snug">{toast.text}</span>
          <button
            type="button"
            onClick={clear}
            className="ml-1 text-[var(--muted)] hover:text-[var(--text)]"
          >
            <X size={14} />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function useTheme() {
  const [dark, setDark] = useState(
    () => localStorage.getItem("nav-theme") !== "light",
  );
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("nav-theme", dark ? "dark" : "light");
  }, [dark]);
  return { dark, toggle: () => setDark((d) => !d) };
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const { dark, toggle } = useTheme();
  const tab = useUi((s) => s.tab);
  const setTab = useUi((s) => s.setTab);
  const ok = useUi((s) => s.ok);
  const err = useUi((s) => s.err);
  const clearToast = useUi((s) => s.clear);

  const demo = useDemoSession((s) => s.demo);
  const ending = useDemoSession((s) => s.ending);
  const hydrate = useDemoSession((s) => s.hydrate);
  const refreshActive = useDemoSession((s) => s.refreshActive);
  const endSession = useDemoSession((s) => s.end);
  const live = demoIsLive(demo);

  useEffect(() => {
    let alive = true;
    api.checkAuth().then((pass) => {
      if (alive) setAuthed(pass);
    });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!authed) return;
    let alive = true;
    (async () => {
      await hydrate();
      if (!alive) return;
    })();
    const t = setInterval(() => {
      void refreshActive();
    }, 1500);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [authed, hydrate, refreshActive]);

  const enterAuthed = () => {
    // Overwrite any leftover "Signed out." from a prior logout (zustand persists across trees).
    clearToast();
    setAuthed(true);
    ok("Signed in.");
  };

  const signOut = async () => {
    if (live) {
      try {
        await endSession();
      } catch {
        /* still sign out */
      }
    }
    await api.logout();
    clearToast();
    setAuthed(false);
    ok("Signed out.");
  };

  const endLive = async () => {
    try {
      await endSession();
      ok("Demo ended.");
    } catch (e) {
      err(errText(e));
    }
  };

  if (authed === null) {
    return (
      <div className="flex min-h-screen items-center justify-center text-[0.8rem] text-[var(--muted)]">
        Checking session…
      </div>
    );
  }

  // Toast stays mounted across auth switches so messages don't stick to the wrong screen.
  if (!authed) {
    return (
      <>
        <AuthScreen onAuthed={enterAuthed} dark={dark} toggleTheme={toggle} />
        <Toast />
      </>
    );
  }

  const Panel = PANELS[tab] ?? Overview;
  const title = TABS.find((t) => t.id === tab)?.label ?? "Overview";
  const subtitles: Record<string, string> = {
    overview: "High-level metrics and recent demo activity.",
    demo: "Start and monitor headful browser sessions in real-time.",
    logs: "Detailed action logs and transcripts for all demos.",
    flows: "Configure automated steps and sequences.",
    graph: "Edit the site graph and page topology.",
    knowledge: "Manage knowledge snippets available to the agent.",
    bio: "Define company identity and product details.",
  };
  const subtitle = subtitles[tab] ?? "Configure your product settings.";

  return (
    <>
      <div className="flex min-h-screen">
        <Sidebar onLogout={signOut} />
        <div className="min-w-0 flex-1">
          <header
            className="sticky top-0 z-30 border-b backdrop-blur-md"
            style={{
              borderColor: "var(--line)",
              background: "color-mix(in oklch, var(--bg) 72%, transparent)",
            }}
          >
            <div className="flex items-center justify-between gap-4 px-5 py-4 md:px-8 md:py-5">
              <div className="min-w-0">
                <h1 className="truncate text-[1.35rem] font-semibold tracking-tighter md:text-[1.6rem]">
                  {title}
                </h1>
                <p className="mt-1 text-[0.79rem] text-[var(--muted)]">
                  {subtitle}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Button variant="ghost" onClick={signOut}>
                  <LogOut size={14} strokeWidth={1.9} />
                  Log out
                </Button>
                <button
                  type="button"
                  onClick={toggle}
                  aria-label="Toggle theme"
                  className="rounded-lg border p-2 text-[var(--muted)] hover:text-[var(--text)]"
                  style={{ borderColor: "var(--line)" }}
                >
                  {dark ? <Sun size={15} /> : <Moon size={15} />}
                </button>
              </div>
            </div>
            <MobileTabs />
            {live && demo && (
              <div
                className="flex flex-wrap items-center gap-3 border-t px-5 py-2.5 md:px-8"
                style={{
                  borderColor: "var(--line)",
                  background: "color-mix(in oklch, var(--accent) 8%, transparent)",
                }}
              >
                <PlayCircle size={14} className="text-[var(--muted)]" />
                <StatusPill status={demo.status} />
                <span className="text-[0.78rem] text-[var(--muted)]">
                  Live demo · {demo.platform || "meeting"} · {demo.page_id || "…"}
                </span>
                <div className="ml-auto flex gap-2">
                  {tab !== "demo" && (
                    <Button
                      variant="secondary"
                      onClick={() => setTab("demo")}
                      className="border-emerald-500/30 text-emerald-600 hover:bg-emerald-500/10 dark:text-emerald-400"
                    >
                      <span className="relative flex h-2 w-2">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                        <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
                      </span>
                      View Live demo
                    </Button>
                  )}
                  <Button variant="danger" disabled={ending} onClick={endLive}>
                    <PhoneOff size={14} />
                    {ending ? "Ending…" : "End demo"}
                  </Button>
                </div>
              </div>
            )}
          </header>

          <main className="px-5 py-6 md:px-8 md:py-8">
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.div
                key={tab}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={soft}
              >
                <Panel />
              </motion.div>
            </AnimatePresence>
          </main>
        </div>
      </div>
      <Toast />
    </>
  );
}
