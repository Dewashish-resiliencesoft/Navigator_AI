import { AlertTriangle, Check, Circle, Loader2, ArrowRight } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { cn } from "../lib/cn";
import { hasCoach } from "../lib/coachTargets";

export type ChecklistTone = "pending" | "running" | "ok" | "warn" | "fail";

export type ChecklistItem = {
  id: string;
  label: string;
  detail?: string;
  status: ChecklistTone;
};

const TONE: Record<
  ChecklistTone,
  { box: string; icon: string; label: string; aria: string }
> = {
  pending: {
    box: "border-[var(--line)] bg-transparent text-[var(--muted)]",
    icon: "text-[var(--muted)]",
    label: "text-[var(--muted)]",
    aria: "Not done",
  },
  running: {
    box: "border-sky-500/35 bg-sky-500/8 text-sky-800 dark:text-sky-300",
    icon: "text-sky-600 dark:text-sky-400",
    label: "text-[var(--text)]",
    aria: "In progress",
  },
  ok: {
    box: "border-emerald-500/35 bg-emerald-500/8 text-emerald-800 dark:text-emerald-300",
    icon: "text-emerald-600 dark:text-emerald-400",
    label: "text-[var(--text)]",
    aria: "Done",
  },
  warn: {
    box: "border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-300",
    icon: "text-amber-600 dark:text-amber-400",
    label: "text-[var(--text)]",
    aria: "Needs attention",
  },
  fail: {
    box: "border-red-500/35 bg-red-500/8 text-red-800 dark:text-red-300",
    icon: "text-red-600 dark:text-red-400",
    label: "text-[var(--text)]",
    aria: "Failed",
  },
};

function StatusGlyph({ status }: { status: ChecklistTone }) {
  const cls = cn("shrink-0", TONE[status].icon);
  if (status === "ok") return <Check size={14} strokeWidth={2.4} className={cls} aria-hidden />;
  if (status === "running")
    return <Loader2 size={14} strokeWidth={2.2} className={cn(cls, "animate-spin")} aria-hidden />;
  if (status === "warn" || status === "fail")
    return <AlertTriangle size={14} strokeWidth={2.2} className={cls} aria-hidden />;
  return <Circle size={14} strokeWidth={1.8} className={cls} aria-hidden />;
}

/** Soft-UI status rows — pending □ / ok ✓ / warn ! / fail / running. */
export function StatusChecklist({
  items,
  className,
  columns = 1,
  onFix,
}: {
  items: ChecklistItem[];
  className?: string;
  columns?: 1 | 2;
  /** When set, open rows with a coach map get a Fix button. */
  onFix?: (checkId: string) => void;
}) {
  if (!items.length) return null;
  return (
    <ul
      className={cn(
        "grid gap-2",
        columns === 2 && "sm:grid-cols-2",
        className,
      )}
      role="list"
    >
      <AnimatePresence initial={false} mode="popLayout">
        {items.map((item) => {
          const tone = TONE[item.status];
          const showFix =
            !!onFix && item.status !== "ok" && hasCoach(item.id);
          return (
            <motion.li
              key={item.id}
              layout
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0, height: "auto" }}
              exit={{
                opacity: 0,
                y: -8,
                height: 0,
                marginBottom: 0,
                paddingTop: 0,
                paddingBottom: 0,
                borderWidth: 0,
              }}
              transition={{ duration: 0.22, ease: "easeOut" }}
              className={cn(
                "flex min-h-11 items-start gap-2.5 overflow-hidden rounded-xl border px-3 py-2.5",
                tone.box,
              )}
              aria-label={`${tone.aria}: ${item.label}`}
            >
              <span
                className={cn(
                  "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border bg-white/50 dark:bg-black/20",
                  "border-current/20",
                )}
                aria-hidden
              >
                <StatusGlyph status={item.status} />
              </span>
              <span className="min-w-0 flex-1">
                <span className={cn("block text-[0.8rem] font-medium leading-snug", tone.label)}>
                  {item.label}
                </span>
                {item.detail ? (
                  <span className="mt-0.5 block text-[0.72rem] leading-snug text-[var(--muted)]">
                    {item.detail}
                  </span>
                ) : null}
              </span>
              {showFix ? (
                <button
                  type="button"
                  className={cn(
                    "mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-lg border px-2 py-1",
                    "text-[0.7rem] font-medium text-[var(--text)]",
                    "border-[var(--line)] bg-white/60 hover:bg-white dark:bg-black/30 dark:hover:bg-black/50",
                  )}
                  onClick={() => onFix?.(item.id)}
                >
                  Fix
                  <ArrowRight size={12} strokeWidth={2.2} aria-hidden />
                </button>
              ) : null}
            </motion.li>
          );
        })}
      </AnimatePresence>
    </ul>
  );
}

export function readinessToChecklist(
  checks: { id: string; message: string; ok: boolean; blocking: boolean }[],
): ChecklistItem[] {
  return checks.map((c) => ({
    id: c.id,
    label: c.message,
    status: c.ok ? "ok" : c.blocking ? "fail" : "warn",
  }));
}
