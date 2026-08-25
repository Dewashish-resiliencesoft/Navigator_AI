/** Floating explore progress — visible on every tab except Flows while explore runs. */

import { motion } from "motion/react";
import { Compass, GripVertical, Square } from "lucide-react";
import { Button } from "./ui";
import { useUi, errText } from "../store";
import {
  exploreIsLive,
  formatExploreElapsed,
  useExploreElapsed,
  useExploreSession,
} from "../lib/exploreSession";

export function ExploreFloat() {
  const tab = useUi((s) => s.tab);
  const setTab = useUi((s) => s.setTab);
  const ok = useUi((s) => s.ok);
  const err = useUi((s) => s.err);

  const status = useExploreSession((s) => s.status);
  const showMeter = useExploreSession((s) => s.showMeter);
  const exploreElapsed = useExploreElapsed();
  const question = useExploreSession((s) => s.question);
  const saveMode = useExploreSession((s) => s.saveMode);
  const targetFlowId = useExploreSession((s) => s.targetFlowId);
  const targetFlowName = useExploreSession((s) => s.targetFlowName);
  const stop = useExploreSession((s) => s.stop);

  const live = exploreIsLive({ status, showMeter });

  // Only float when explore is live and user left Flows (in-panel meter owns Flows).
  if (!live || tab === "flows") return null;

  const pct = Math.min(100, Math.max(0, Math.round(status.progress_pct ?? 0)));
  const left = Math.max(0, 100 - pct);
  const pages = status.visited ?? 0;
  const maxPages = status.budget?.max_pages ?? 25;
  const mode = status.save_mode === "update" || saveMode === "update" ? "update" : "new";
  const flowLabel =
    status.target_flow_name ||
    targetFlowName ||
    status.target_flow_id ||
    targetFlowId ||
    "flow";

  const onStop = async () => {
    try {
      await stop();
      ok("Stopping — draft steps saved.");
    } catch (e) {
      err(errText(e));
    }
  };

  return (
    <motion.div
      drag
      dragMomentum={false}
      dragElastic={0.12}
      initial={{ opacity: 0, scale: 0.94 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ type: "spring", stiffness: 320, damping: 26 }}
      className="fixed bottom-6 right-6 z-40 w-[min(20rem,calc(100vw-1.5rem))] cursor-grab active:cursor-grabbing rounded-2xl border p-3.5 shadow-lg backdrop-blur-xl"
      style={{
        borderColor: "var(--line)",
        background:
          "linear-gradient(145deg, color-mix(in oklab, var(--accent) 12%, var(--panel)), color-mix(in oklch, var(--panel) 90%, transparent))",
      }}
    >
      <div className="mb-2.5 flex items-center gap-2">
        <GripVertical size={14} className="shrink-0 text-[var(--muted)]" />
        <Compass size={14} className="text-[var(--accent)]" />
        <p className="min-w-0 flex-1 truncate text-[0.78rem] font-semibold tracking-tight">
          Exploring
          {question ? " · waiting on you" : ""}
        </p>
        <span className="font-mono text-[0.85rem] font-semibold tabular-nums">
          {formatExploreElapsed(exploreElapsed)}
        </span>
      </div>

      <p className="mb-2 truncate text-[0.68rem] text-[var(--muted)]">
        {mode === "update" ? (
          <>
            Updating <span className="font-medium text-[var(--text)]">“{flowLabel}”</span>
          </>
        ) : (
          <>Creating <span className="font-medium text-[var(--text)]">new flow</span></>
        )}
      </p>
      <div className="mb-1 flex items-end justify-between gap-2">
        <div>
          <p className="text-[1.35rem] font-semibold tracking-tight tabular-nums leading-none">
            {pct}%
          </p>
          <p className="mt-0.5 text-[0.65rem] uppercase tracking-[0.08em] text-[var(--muted)]">
            done
          </p>
        </div>
        <div className="text-right">
          <p className="text-[1.05rem] font-semibold tracking-tight tabular-nums leading-none text-[var(--muted)]">
            {left}%
          </p>
          <p className="mt-0.5 text-[0.65rem] uppercase tracking-[0.08em] text-[var(--muted)]">
            left
          </p>
        </div>
      </div>

      <div
        className="mb-2 h-1.5 overflow-hidden rounded-full"
        style={{ background: "color-mix(in oklab, var(--line) 80%, transparent)" }}
      >
        <motion.div
          className="h-full rounded-full bg-[var(--accent)]"
          initial={false}
          animate={{ width: `${pct}%` }}
          transition={{ type: "spring", stiffness: 90, damping: 18 }}
        />
      </div>

      <p className="mb-3 text-[0.68rem] text-[var(--muted)] tabular-nums">
        {pages}/{maxPages} pages · {status.steps ?? 0} demo steps
        {status.actions_taken != null ? ` · ${status.actions_taken} actions` : ""}
      </p>

      <div className="flex flex-wrap gap-2">
        <div
          className="flex-1"
          onPointerDown={(e) => e.stopPropagation()}
        >
          <Button
            variant="secondary"
            className="w-full"
            onClick={() => setTab("flows")}
          >
            Open Flows
          </Button>
        </div>
        <div onPointerDown={(e) => e.stopPropagation()}>
          <Button variant="danger" onClick={onStop}>
            <Square size={12} /> Stop
          </Button>
        </div>
      </div>
    </motion.div>
  );
}
