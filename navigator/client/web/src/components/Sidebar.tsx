import { motion } from "motion/react";
import {
  Activity,
  BookOpen,
  Building2,
  ListOrdered,
  LogOut,
  Network,
  PlayCircle,
  ScrollText,
} from "lucide-react";
import { cn } from "../lib/cn";
import { soft } from "../lib/motion";
import { useUi } from "../store";

export const TABS = [
  { id: "overview", label: "Overview", icon: Activity },
  { id: "demo", label: "Live demo", icon: PlayCircle },
  { id: "logs", label: "Logs", icon: ScrollText },
  { id: "flows", label: "Flows", icon: ListOrdered },
  { id: "graph", label: "Site graph", icon: Network },
  { id: "knowledge", label: "Knowledge", icon: BookOpen },
  { id: "bio", label: "Company bio", icon: Building2 },
] as const;

export function Sidebar({ onLogout }: { onLogout?: () => void }) {
  const { tab, setTab } = useUi();

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
                "text-[0.83rem] font-medium tracking-tight",
                active ? "text-[var(--text)]" : "text-[var(--muted)] hover:text-[var(--text)]",
              )}
            >
              {active && (
                <motion.span
                  layoutId="nav-capsule"
                  transition={soft}
                  className="absolute inset-0 rounded-lg border bg-black/[0.045] dark:bg-white/[0.07]"
                  style={{ borderColor: "var(--line)" }}
                />
              )}
              <Icon size={15} strokeWidth={1.9} className="relative shrink-0" />
              <span className="relative">{label}</span>
            </button>
          );
        })}
      </nav>

      <div className="mt-auto space-y-3 px-1">
        {onLogout && (
          <button
            type="button"
            onClick={onLogout}
            className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-[0.8rem] font-medium text-[var(--muted)] hover:bg-black/[0.04] hover:text-[var(--text)] dark:hover:bg-white/[0.06]"
          >
            <LogOut size={14} strokeWidth={1.9} />
            Log out
          </button>
        )}
        <p className="text-[0.68rem] leading-relaxed text-[var(--muted)]">
          Loopback only. Bound to localhost.
        </p>
      </div>
    </aside>
  );
}

export function MobileTabs() {
  const { tab, setTab } = useUi();
  return (
    <div
      className="flex gap-1 overflow-x-auto border-b px-4 py-2 md:hidden"
      style={{ borderColor: "var(--line)" }}
    >
      {TABS.map(({ id, label }) => (
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
