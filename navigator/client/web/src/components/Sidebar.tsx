import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import {
  Activity,
  BookOpen,
  Building2,
  ListOrdered,
  LogOut,
  Network,
  PlayCircle,
  ScrollText,
  Settings2,
} from "lucide-react";
import { cn } from "../lib/cn";
import { soft } from "../lib/motion";
import { useAccountSession } from "../lib/accountSession";
import { useUi } from "../store";
import { GetStartedCard } from "./GetStartedCard";
import {
  loadUserPreferences,
  showOnboardingCard,
  isOnboardingCardHidden,
  type OnboardingItemId,
} from "../lib/onboarding";
import { useOnboardingProgress } from "../lib/useOnboardingProgress";

export const TABS = [
  { id: "overview", label: "Overview", icon: Activity },
  { id: "demo", label: "Live demo", icon: PlayCircle },
  { id: "logs", label: "Logs", icon: ScrollText },
  { id: "flows", label: "Flows", icon: ListOrdered },
  { id: "graph", label: "Site graph", icon: Network },
  { id: "knowledge", label: "Knowledge", icon: BookOpen },
  { id: "bio", label: "Company bio", icon: Building2 },
  { id: "settings", label: "Settings", icon: Settings2 },
] as const;

/** Bouncy expand/shrink for account chip. */
const bubbleSpring = {
  type: "spring" as const,
  stiffness: 480,
  damping: 15,
  mass: 0.65,
};

export function Sidebar({
  onLogout,
  onContinueSetup,
}: {
  onLogout?: () => void;
  onContinueSetup?: (startAt: OnboardingItemId | null) => void;
}) {
  const { tab, setTab } = useUi();
  const { progress } = useOnboardingProgress();
  const [cardHidden, setCardHidden] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const accountRef = useRef<HTMLDivElement>(null);
  const email = useAccountSession((s) => s.email);
  const productName = useAccountSession((s) => s.productName);
  const productId = useAccountSession((s) => s.productId);
  const accountLoading = useAccountSession((s) => s.loading);
  const refreshAccount = useAccountSession((s) => s.refresh);

  useEffect(() => {
    let alive = true;
    (async () => {
      await loadUserPreferences();
      if (!alive) return;
      setCardHidden(isOnboardingCardHidden());
      await refreshAccount();
    })();
    return () => {
      alive = false;
    };
  }, [progress?.percent, progress?.complete, refreshAccount]);

  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!accountRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const setupIncomplete = progress && !progress.complete;
  const showSetupLink = setupIncomplete && cardHidden && onContinueSetup;
  const emailLabel = email || (accountLoading ? "Loading…" : "—");
  const productLabel = productName || (accountLoading ? "…" : productId || "—");

  return (
    <aside
      className="sticky top-0 hidden h-screen w-[236px] shrink-0 flex-col border-r p-5 md:flex"
      style={{ borderColor: "var(--line)" }}
    >
      <div className="mb-8 flex items-center gap-2.5 px-1">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--text)] text-[var(--bg)]">
          <Network size={15} strokeWidth={2.2} />
        </div>
        <div className="leading-tight">
          <p className="text-[0.85rem] font-semibold tracking-tight">Navigator AI</p>
          <p className="text-[0.68rem] text-[var(--muted)]">Client console</p>
        </div>
      </div>

      <nav className="flex flex-col gap-0.5">
        {TABS.map(({ id, label, icon: Icon }) => {
          const active = tab === id;
          return (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              className={cn(
                "relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-left",
                "text-[0.83rem] font-medium tracking-tight transition-colors",
                active
                  ? "text-[var(--text)]"
                  : "text-[var(--muted)] hover:bg-black/[0.04] hover:text-[var(--text)] dark:hover:bg-white/[0.06]",
              )}
            >
              {active && (
                <motion.span
                  layoutId="nav-capsule"
                  transition={soft}
                  className="absolute inset-0 rounded-lg border border-l-[3px] border-l-[var(--accent)] bg-black/[0.045] dark:bg-white/[0.07]"
                  style={{ borderColor: "var(--line)", borderLeftColor: "var(--accent)" }}
                />
              )}
              <Icon size={15} strokeWidth={1.9} className="relative shrink-0" />
              <span className="relative">{label}</span>
            </button>
          );
        })}
      </nav>

      <div className="mt-auto space-y-3 px-1">
        {onContinueSetup && <GetStartedCard onContinue={onContinueSetup} />}
        {showSetupLink && (
          <button
            type="button"
            onClick={() => {
              void showOnboardingCard().then(() => {
                setCardHidden(false);
                onContinueSetup?.(null);
              });
            }}
            className="w-full rounded-lg border px-2.5 py-2 text-left text-[0.75rem] font-medium text-[var(--muted)] hover:text-[var(--text)]"
            style={{ borderColor: "var(--line)" }}
          >
            Show setup guide
          </button>
        )}
        <button
          type="button"
          onClick={() => setTab("monitor")}
          className={cn(
            "flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-[0.8rem] font-medium transition-colors",
            tab === "monitor"
              ? "bg-black/[0.06] text-[var(--text)] dark:bg-white/[0.08]"
              : "text-[var(--muted)] hover:bg-black/[0.04] hover:text-[var(--text)] dark:hover:bg-white/[0.06]",
          )}
        >
          <Activity size={14} strokeWidth={1.9} />
          Resource Monitor & Health Check
        </button>

        {/* Same chip expands in place — no floating second container */}
        <motion.div
          ref={accountRef}
          layout
          transition={bubbleSpring}
          animate={{
            scale: menuOpen ? 1.04 : 1,
            y: menuOpen ? -2 : 0,
          }}
          className={cn(
            "origin-bottom overflow-hidden rounded-2xl border bg-[var(--bg)] shadow-sm",
            menuOpen && "shadow-lg",
          )}
          style={{ borderColor: menuOpen ? "color-mix(in oklch, var(--accent) 45%, var(--line))" : "var(--line)" }}
        >
          <button
            type="button"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((o) => !o)}
            className="w-full px-2.5 py-2 text-left"
          >
            <p
              className="truncate text-[0.75rem] font-medium text-[var(--text)]"
              title={email || undefined}
            >
              {emailLabel}
            </p>
            <p
              className="truncate text-[0.68rem] text-[var(--muted)]"
              title={productName || productId || undefined}
            >
              {productLabel}
            </p>
          </button>

          <AnimatePresence initial={false}>
            {menuOpen && (
              <motion.div
                key="account-extra"
                initial={{ height: 0, opacity: 0, filter: "blur(10px)" }}
                animate={{ height: "auto", opacity: 1, filter: "blur(0px)" }}
                exit={{ height: 0, opacity: 0, filter: "blur(10px)" }}
                transition={bubbleSpring}
                className="overflow-hidden"
              >
                <div
                  className="space-y-2 border-t px-2.5 pb-2.5 pt-2"
                  style={{ borderColor: "var(--line)" }}
                >
                  {email ? (
                    <p className="break-all text-[0.72rem] text-[var(--muted)]">{email}</p>
                  ) : null}
                  {productId ? (
                    <p className="font-mono text-[0.65rem] text-[var(--muted)]">{productId}</p>
                  ) : null}
                  {onLogout && (
                    <button
                      type="button"
                      onClick={() => {
                        setMenuOpen(false);
                        onLogout();
                      }}
                      className="flex w-full items-center justify-center gap-2 rounded-xl border px-2.5 py-2 text-[0.78rem] font-medium text-[var(--text)] hover:bg-black/[0.04] dark:hover:bg-white/[0.06]"
                      style={{ borderColor: "var(--line)" }}
                    >
                      <LogOut size={14} strokeWidth={1.9} />
                      Log out
                    </button>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>

        <p className="text-[0.68rem] leading-relaxed text-[var(--muted)]">Navigator AI</p>
      </div>
    </aside>
  );
}

export function MobileTabs() {
  const { tab, setTab } = useUi();
  const items = [...TABS, { id: "monitor" as const, label: "Monitor" }];
  return (
    <div
      className="flex gap-1 overflow-x-auto border-b px-4 py-2 md:hidden"
      style={{ borderColor: "var(--line)" }}
    >
      {items.map(({ id, label }) => (
        <button
          key={id}
          type="button"
          onClick={() => setTab(id)}
          className={cn(
            "relative shrink-0 rounded-lg px-3 py-1.5 text-[0.78rem] font-medium",
            tab === id ? "text-[var(--text)]" : "text-[var(--muted)]",
          )}
        >
          {tab === id && (
            <motion.span
              layoutId="nav-capsule-mobile"
              transition={soft}
              className="absolute inset-0 rounded-lg bg-black/[0.05] dark:bg-white/[0.08]"
            />
          )}
          <span className="relative">{label}</span>
        </button>
      ))}
    </div>
  );
}
