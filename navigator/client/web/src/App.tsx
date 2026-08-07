import { lazy, Suspense, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AlertCircle, CheckCircle2, LogOut, Moon, PhoneOff, PlayCircle, Sun, X } from "lucide-react";
import { MobileTabs, Sidebar, TABS } from "./components/Sidebar";
import { Overview } from "./panels/Overview";
import { LiveDemo } from "./panels/LiveDemo";
import { Logs } from "./panels/Logs";
import { Flows } from "./panels/Flows";
import { Execution } from "./panels/Execution";
import { Bio, Knowledge, SiteGraph } from "./panels/Editors";
import { Settings } from "./panels/Settings";
import { ResourceMonitor } from "./panels/ResourceMonitor";
import { ExploreFloat } from "./components/ExploreFloat";
import { soft } from "./lib/motion";
import { errText, useUi } from "./store";
import { api } from "./lib/api";
import { demoIsLive, useDemoSession } from "./lib/demoSession";
import { useExploreSession } from "./lib/exploreSession";
import { useProductData } from "./lib/productData";
import { AuthScreen } from "./panels/AuthScreen";
import { OnboardingWizard } from "./panels/Onboarding";
import { Button, StatusPill } from "./components/ui";
import { DISPLAY_TICK_MS } from "./lib/elapsed";
import {
  markSignupPending,
  shouldAutoOpenWizard,
  loadUserPreferences,
  type OnboardingItemId,
} from "./lib/onboarding";

const ConfettiCelebration = lazy(() =>
  import("./components/ConfettiCelebration").then((m) => ({
    default: m.ConfettiCelebration,
  })),
);

const PANELS: Record<string, () => React.ReactElement> = {
  overview: Overview,
  demo: LiveDemo,
  logs: Logs,
  flows: Flows,
  execution: Execution,
  graph: SiteGraph,
  knowledge: Knowledge,
  bio: Bio,
  settings: Settings,
  monitor: ResourceMonitor,
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
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [showConfetti, setShowConfetti] = useState(false);
  const [onboardingStartAt, setOnboardingStartAt] = useState<OnboardingItemId | null>(null);
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

  const hydrateExplore = useExploreSession((s) => s.hydrate);

  const celebrateOnboardingComplete = () => {
    setShowConfetti(true);
    ok("Onboarding completed");
  };

  useEffect(() => {
    let alive = true;
    api.checkAuth().then(async (pass) => {
      if (!alive) return;
      if (pass) {
        try {
          await loadUserPreferences();
        } catch {
          /* prefs optional */
        }
      }
      setAuthed(pass);
    });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!authed) return;
    if (shouldAutoOpenWizard()) {
      setShowOnboarding(true);
    }
  }, [authed]);

  useEffect(() => {
    if (!authed) return;
    let alive = true;
    (async () => {
      await hydrate();
      if (!alive) return;
      await hydrateExplore();
    })();
    const t = setInterval(() => {
      void refreshActive();
    }, DISPLAY_TICK_MS * 2);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [authed, hydrate, hydrateExplore, refreshActive]);

  const enterAuthed = (fromSignup = false, company = "") => {
    // Overwrite any leftover "Signed out." from a prior logout (zustand persists across trees).
    clearToast();
    if (fromSignup) {
      markSignupPending(company);
      setShowOnboarding(true);
      setOnboardingStartAt(null);
    }
    setAuthed(true);
    ok(fromSignup ? "Account created." : "Signed in.");
  };

  const openOnboarding = (startAt: OnboardingItemId | null) => {
    setOnboardingStartAt(startAt);
    setShowOnboarding(true);
  };

  const closeOnboarding = () => {
    setShowOnboarding(false);
    setOnboardingStartAt(null);
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
    useProductData.getState().reset();
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
  const title =
    tab === "monitor"
      ? "Resource Monitor & Health Check"
      : (TABS.find((t) => t.id === tab)?.label ?? "Overview");
  const subtitles: Record<string, string> = {
    overview: "High-level metrics and recent demo activity.",
    demo: "Start and monitor headful browser sessions in real-time.",
    logs: "Detailed action logs and transcripts for all demos.",
    flows: "Configure automated steps and sequences.",
    execution: "Explore scope and mutating-step approvals before live demo.",
    graph: "Edit the site graph and page topology.",
    knowledge: "Manage knowledge snippets available to the agent.",
    bio: "Define company identity and product details.",
    monitor: "Real-time CPU, memory, network, GPU, and service health on this host.",
  };
  const subtitle = subtitles[tab] ?? "Configure your product settings.";

  return (
    <>
      <div className="flex min-h-screen">
        <Sidebar onLogout={signOut} onContinueSetup={openOnboarding} />
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
      <ExploreFloat />
      {authed && showConfetti && (
        <Suspense fallback={null}>
          <ConfettiCelebration
            show={showConfetti}
            onDone={() => setShowConfetti(false)}
          />
        </Suspense>
      )}
      {showOnboarding && (
        <OnboardingWizard
          startAt={onboardingStartAt}
          onClose={closeOnboarding}
          onFullyComplete={celebrateOnboardingComplete}
        />
      )}
    </>
  );
}
