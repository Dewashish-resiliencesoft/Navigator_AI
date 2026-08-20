/** Docked bottom-right Product Explore progress — bounce sheet while Client works. */

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CheckCircle2, Loader2, Search, Sparkles, Square } from "lucide-react";
import { Button } from "./ui";
import { errText, useUi } from "../store";
import {
  productExploreIsLive,
  productExplorePct,
  useProductExploreSession,
} from "../lib/productExploreSession";

const bounce = {
  type: "spring" as const,
  stiffness: 560,
  damping: 14,
  mass: 0.65,
};

function MagnifierSparkle({ success }: { success?: boolean }) {
  if (success) {
    return (
      <div className="relative flex h-10 w-10 shrink-0 items-center justify-center text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 size={22} strokeWidth={2.2} />
      </div>
    );
  }
  return (
    <div className="relative flex h-10 w-10 shrink-0 items-center justify-center">
      <motion.span
        className="absolute inset-0 rounded-full border border-sky-500/30"
        animate={{ scale: [1, 1.18, 1], opacity: [0.5, 0.15, 0.5] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        animate={{ rotate: [0, -12, 8, 0], y: [0, -2, 1, 0] }}
        transition={{ duration: 2.2, repeat: Infinity, ease: "easeInOut" }}
        className="relative text-sky-600 dark:text-sky-400"
      >
        <Search size={18} strokeWidth={2.1} />
      </motion.div>
      <motion.span
        className="absolute -right-0.5 -top-0.5 text-amber-500"
        animate={{ opacity: [0.2, 1, 0.2], scale: [0.7, 1.15, 0.7] }}
        transition={{ duration: 1.1, repeat: Infinity }}
      >
        <Sparkles size={11} strokeWidth={2.2} />
      </motion.span>
    </div>
  );
}

function siteLabel(url?: string, title?: string): { key: string; label: string } | null {
  const raw = (url || "").trim();
  if (!raw) {
    const t = (title || "").trim();
    return t ? { key: t, label: t } : null;
  }
  try {
    const u = new URL(raw.includes("://") ? raw : `https://${raw}`);
    const host = u.hostname.replace(/^www\./, "");
    const path = u.pathname && u.pathname !== "/" ? u.pathname : "";
    return {
      key: host + path,
      label: host + (path.length > 24 ? path.slice(0, 24) + "…" : path),
    };
  } catch {
    return { key: raw, label: raw };
  }
}

function stepCopy(status: {
  phase?: string;
  looking_at?: string;
  error?: string | null;
}): { key: string; text: string; success: boolean } {
  const phase = status.phase || "";
  if (phase === "error" || status.error) {
    return {
      key: "error",
      text: status.looking_at || status.error || "Explore failed",
      success: false,
    };
  }
  if (phase === "done") {
    return {
      key: "done",
      text: status.looking_at || "Company bio and knowledge updated",
      success: true,
    };
  }
  if (phase === "writing_bio") {
    return { key: "bio", text: status.looking_at || "Filling company bio…", success: false };
  }
  if (phase === "updating_knowledge") {
    return {
      key: "knowledge",
      text: status.looking_at || "Updating knowledge…",
      success: false,
    };
  }
  return {
    key: phase || "work",
    text: status.looking_at || phase || "Working…",
    success: false,
  };
}

export function ProductExploreFloat() {
  const setTab = useUi((s) => s.setTab);
  const ok = useUi((s) => s.ok);
  const err = useUi((s) => s.err);
  const status = useProductExploreSession((s) => s.status);
  const stopping = useProductExploreSession((s) => s.stopping);
  const stop = useProductExploreSession((s) => s.stop);
  const live = productExploreIsLive(status);
  const pct = productExplorePct(status);
  const site = siteLabel(status.current_url, status.current_title);
  const step = stepCopy(status);
  const finishing =
    status.phase === "writing_bio" ||
    status.phase === "updating_knowledge" ||
    status.phase === "done";
  const [sheetOpen, setSheetOpen] = useState(false);

  useEffect(() => {
    if (status.active) {
      setSheetOpen(true);
      return;
    }
    if (status.phase === "done" && !status.error) {
      setSheetOpen(true);
      const t = window.setTimeout(() => setSheetOpen(false), 2000);
      return () => window.clearTimeout(t);
    }
    if (status.phase === "error" || status.error) {
      const t = window.setTimeout(() => setSheetOpen(false), 2200);
      return () => window.clearTimeout(t);
    }
    setSheetOpen(false);
  }, [status.active, status.phase, status.error]);

  const onStop = async () => {
    try {
      await stop();
      ok("Product Explore stopped.");
    } catch (e) {
      err(errText(e));
    }
  };

  return (
    <AnimatePresence>
      {sheetOpen && (
        <motion.aside
          key="product-explore-float"
          role="status"
          aria-live="polite"
          aria-label="Product Explore progress"
          initial={{ y: 96, scale: 0.72, opacity: 0, originX: 1, originY: 1 }}
          animate={{ y: 0, scale: 1, opacity: 1, originX: 1, originY: 1 }}
          exit={{ y: 110, scale: 0.68, opacity: 0, originX: 1, originY: 1 }}
          transition={bounce}
          className="fixed bottom-0 right-4 z-40 w-[min(22rem,calc(100vw-1.5rem))] overflow-hidden rounded-t-2xl border border-b-0 shadow-[0_-8px_32px_-12px_rgba(0,0,0,0.35)] backdrop-blur-xl"
          style={{
            borderColor: "var(--line)",
            background:
              "linear-gradient(165deg, color-mix(in oklch, var(--panel) 92%, transparent), color-mix(in oklch, var(--accent) 8%, var(--panel)))",
          }}
        >
          <div className="mx-auto mb-0 mt-1.5 h-1 w-10 rounded-full bg-black/15 dark:bg-white/20" />
          <div className="flex items-start gap-3 px-3.5 pb-3.5 pt-2">
            <MagnifierSparkle success={step.success} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <p className="truncate text-[0.8rem] font-semibold tracking-tight">
                  Product Explore
                </p>
                <span
                  className={
                    "ml-auto font-mono text-[0.78rem] font-semibold tabular-nums " +
                    (step.success
                      ? "text-emerald-700 dark:text-emerald-300"
                      : "text-sky-700 dark:text-sky-300")
                  }
                >
                  {step.success ? "100%" : `${pct}%`}
                </span>
              </div>

              <div className="mt-0.5 min-h-[1.1rem]">
                <AnimatePresence mode="wait">
                  <motion.p
                    key={step.key}
                    initial={{ opacity: 0, y: 8, scale: 0.96 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: -8, scale: 0.96 }}
                    transition={{ type: "spring", stiffness: 420, damping: 22 }}
                    className={
                      "line-clamp-2 text-[0.7rem] " +
                      (step.success
                        ? "font-medium text-emerald-700 dark:text-emerald-300"
                        : "text-[var(--muted)]")
                    }
                  >
                    {step.text}
                  </motion.p>
                </AnimatePresence>
              </div>

              {!finishing && (
                <div className="mt-1.5 min-h-[1.35rem]">
                  <AnimatePresence mode="wait">
                    {site ? (
                      <motion.div
                        key={site.key}
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -6 }}
                        transition={{ duration: 0.2 }}
                        className="flex items-center gap-2"
                      >
                        <span className="min-w-0 truncate text-[0.78rem] font-medium text-sky-600 dark:text-sky-400">
                          {site.label}
                        </span>
                        <Loader2
                          size={14}
                          className="ml-auto shrink-0 animate-spin text-sky-500"
                          aria-hidden
                        />
                      </motion.div>
                    ) : (
                      <motion.div
                        key="waiting-site"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="flex items-center gap-2 text-[0.72rem] text-[var(--muted)]"
                      >
                        <span className="min-w-0 truncate">
                          {status.looking_at ||
                            status.current_title ||
                            "Finding next site…"}
                        </span>
                        <Loader2
                          size={14}
                          className="ml-auto shrink-0 animate-spin"
                        />
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              )}

              <div
                className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-black/[0.06] dark:bg-white/[0.08]"
                role="progressbar"
                aria-valuenow={step.success ? 100 : pct}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <motion.div
                  className={
                    "h-full rounded-full " +
                    (step.success ? "bg-emerald-500" : "bg-sky-500")
                  }
                  initial={false}
                  animate={{ width: `${step.success ? 100 : pct}%` }}
                  transition={{ duration: 0.35, ease: "easeOut" }}
                />
              </div>

              <div className="mt-2.5 flex items-center gap-2">
                <button
                  type="button"
                  className="cursor-pointer text-[0.7rem] font-medium text-[var(--accent)] underline-offset-2 hover:underline"
                  onClick={() => setTab("knowledge")}
                >
                  View details
                </button>
                {!step.success && (
                  <span className="text-[0.65rem] text-[var(--muted)]">
                    {status.pages_seen ?? 0}
                    {status.max_pages != null ? ` / ${status.max_pages}` : ""}{" "}
                    pages
                  </span>
                )}
                {live && !step.success && (
                  <Button
                    variant="ghost"
                    className="ml-auto !px-2 !py-1 text-[0.7rem]"
                    disabled={stopping}
                    onClick={() => void onStop()}
                  >
                    {stopping ? (
                      <Loader2 size={12} className="animate-spin" />
                    ) : (
                      <Square size={11} />
                    )}
                    Stop
                  </Button>
                )}
              </div>
            </div>
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
