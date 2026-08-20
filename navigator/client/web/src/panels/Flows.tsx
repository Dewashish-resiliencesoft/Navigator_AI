import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowDown,
  ArrowUp,
  Circle,
  Clock,
  Compass,
  Loader2,
  Plus,
  Save,
  ShieldAlert,
  Square,
  Trash2,
  Wand2,
} from "lucide-react";
import { api, type ExploreFlagged, type Flow, type GuidedHandsStatus, type GuidedTaskStatus, type RecorderStatus } from "../lib/api";
import {
  exploreDraftProgressPct,
  exploreIsLive,
  exploreIsPersisting,
  exploreIsTerminal,
  formatExploreElapsed,
  useExploreElapsed,
  useExploreSession,
} from "../lib/exploreSession";
import { useProductData } from "../lib/productData";
import { soft, stagger } from "../lib/motion";
import { ExploreWatch } from "../components/ExploreWatch";
import {
  BarLoader,
  Button,
  Card,
  CardTitle,
  Empty,
  Field,
  Input,
  Select,
  StatusPill,
  Switch,
  ConfirmDialog,
} from "../components/ui";
import { errText, useUi } from "../store";

const isRecording = (s: RecorderStatus) =>
  !!(s.recording || s.active || s.status === "recording");

function FlowDraftLoader({ pct, label }: { pct: number; label?: string }) {
  const n = Math.min(100, Math.max(0, Math.round(pct)));
  return (
    <div
      className="flex min-w-[4.5rem] flex-col items-end gap-0.5 px-1"
      title={label}
    >
      <Loader2 size={16} className="animate-spin text-[var(--accent)]" />
      <span className="font-mono text-[0.68rem] tabular-nums text-[var(--muted)]">
        {n}%
      </span>
    </div>
  );
}

function FlowFieldCell({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="min-w-0">
      <span className="mb-0.5 block text-[0.62rem] font-medium uppercase tracking-[0.04em] text-[var(--muted)]">
        {label}
      </span>
      {children}
    </label>
  );
}

export function Flows() {
  const { ok, err } = useUi();
  const epoch = useProductData((s) => s.epoch);
  const applyPlaylist = useProductData((s) => s.applyPlaylist);
  const setPlaylist = useProductData((s) => s.setPlaylist);
  const [rows, setRows] = useState<Flow[] | null>(null);
  const [recording, setRecording] = useState(false);
  const [recPhase, setRecPhase] = useState<string>("");
  const [setupDiscarded, setSetupDiscarded] = useState(0);
  const [capturedSteps, setCapturedSteps] = useState(0);
  const [recName, setRecName] = useState("");
  const [recUrl, setRecUrl] = useState("");
  const [recNarrate, setRecNarrate] = useState(true);
  const [recSaveMode, setRecSaveMode] = useState<"new" | "update">("new");
  const [recTargetFlowId, setRecTargetFlowId] = useState("");
  const [recNarrating, setRecNarrating] = useState(false);
  const [narrationChunks, setNarrationChunks] = useState(0);
  const [stepCount, setStepCount] = useState<Record<string, number>>({});
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const [confirmClearAll, setConfirmClearAll] = useState(false);
  const [agentTask, setAgentTask] = useState("");
  const [guidedStatus, setGuidedStatus] = useState<GuidedTaskStatus | null>(null);
  const [guidedPlanning, setGuidedPlanning] = useState(false);
  const [guidedHands, setGuidedHands] = useState<GuidedHandsStatus | null>(null);
  const timer = useRef<number | null>(null);
  const handsTimer = useRef<number | null>(null);

  const exploreStatus = useExploreSession((s) => s.status);
  const exploreSaveMode = useExploreSession((s) => s.saveMode);
  const exploreTargetFlowId = useExploreSession((s) => s.targetFlowId);
  const exploreTargetFlowName = useExploreSession((s) => s.targetFlowName);
  const exploreShowMeter = useExploreSession((s) => s.showMeter);
  const exploreDraftPct = exploreDraftProgressPct(exploreStatus);
  const exploreDrafting =
    exploreStatus.active ||
    exploreIsPersisting(exploreStatus) ||
    (exploreShowMeter && !exploreIsTerminal(exploreStatus.phase) && !exploreStatus.flow_id);
  const exploreUpdatingFlowId =
    exploreDrafting && exploreSaveMode === "update" ? exploreTargetFlowId.trim() : "";
  const pendingNewFlowName =
    exploreStatus.target_flow_name?.trim() ||
    exploreTargetFlowName.trim() ||
    "New explored flow";
  const showPendingNewRow =
    exploreDrafting &&
    exploreSaveMode === "new" &&
    !(
      exploreStatus.flow_id &&
      rows?.some((r) => r.flow_id === exploreStatus.flow_id)
    );
  const recordFlows = (rows ?? []).filter((f) => !!f.flow_id?.trim());

  const load = useCallback(async () => {
    try {
      const [d, g] = await Promise.all([api.getFlows(), api.getSiteGraph()]);
      const playlist = d.playlist ?? [];
      setRows(playlist);
      // ponytail: setPlaylist only — applyPlaylist would re-bump epoch and loop load
      setPlaylist(playlist);
      try {
        const guided = await api.guidedTaskStatus();
        setGuidedStatus(guided);
        setGuidedHands(guided.hands ?? null);
      } catch {
        setGuidedStatus(null);
        setGuidedHands(null);
      }

      const counts: Record<string, number> = {};
      let inFlows = false;
      let currentFlow = null;
      for (const line of g.yaml.split("\n")) {
        if (line.startsWith("flows:")) { inFlows = true; continue; }
        if (line.match(/^[a-z]/)) { inFlows = false; }
        if (!inFlows) continue;
        const m = line.match(/^  ([^:\s]+):/);
        if (m) { currentFlow = m[1]; counts[currentFlow] = 0; }
        else if (currentFlow && line.trim().startsWith("- tool:")) {
          counts[currentFlow]++;
        }
      }
      setStepCount(counts);
    } catch (e) {
      err(errText(e));
    }
  }, [err, setPlaylist]);

  useEffect(() => {
    void load();
  }, [load, epoch]);

  const stopPolling = () => {
    if (timer.current !== null) {
      clearInterval(timer.current);
      timer.current = null;
    }
  };

  useEffect(() => stopPolling, []);

  const stopHandsPolling = () => {
    if (handsTimer.current !== null) {
      clearInterval(handsTimer.current);
      handsTimer.current = null;
    }
  };

  useEffect(() => stopHandsPolling, []);

  const refreshGuided = useCallback(async () => {
    try {
      const g = await api.guidedTaskStatus();
      setGuidedStatus(g);
      setGuidedHands(g.hands ?? null);
    } catch {
      /* optional */
    }
  }, []);

  const pollHands = useCallback(async () => {
    try {
      const g = await api.guidedTaskStatus();
      setGuidedStatus(g);
      setGuidedHands(g.hands ?? null);
      const hands = g.hands;
      if (
        hands?.active &&
        hands.phase !== "awaiting_input" &&
        hands.phase !== "paused" &&
        hands.phase !== "barged" &&
        !hands.client_paused &&
        !hands.barged
      ) {
        await api.guidedHandsTick();
        const again = await api.guidedTaskStatus();
        setGuidedStatus(again);
        setGuidedHands(again.hands ?? null);
        if (!again.hands?.active) stopHandsPolling();
      }
    } catch (e) {
      err(errText(e));
    }
  }, [err]);

  const runGuidedPlan = async () => {
    if (!agentTask.trim()) return err("Describe what the demo should cover.");
    setGuidedPlanning(true);
    try {
      const r = await api.guidedTaskPlan(agentTask.trim());
      setGuidedStatus(r.guided);
      applyPlaylist(r.playlist);
      setRows(r.playlist);
      ok(
        `Plan created — ${r.flows_created} flow${r.flows_created === 1 ? "" : "s"}, ${r.steps_total} steps. Record each flow (Update existing) to bind selectors.`,
      );
    } catch (e) {
      err(errText(e));
    } finally {
      setGuidedPlanning(false);
    }
  };

  const startGuidedHands = async () => {
    if (!recording || recPhase !== "capturing") {
      return err("Start recording and click Start capturing before guided hands.");
    }
    try {
      const h = await api.guidedHandsStart(0);
      setGuidedHands(h);
      stopHandsPolling();
      handsTimer.current = window.setInterval(pollHands, 2000);
      ok("Guided hands running — Pause / Take over anytime; Resume to continue.");
    } catch (e) {
      err(errText(e));
    }
  };

  const stopGuidedHands = async () => {
    try {
      await api.guidedHandsStop();
      stopHandsPolling();
      await refreshGuided();
    } catch (e) {
      err(errText(e));
    }
  };

  const pauseGuidedHands = async () => {
    try {
      const h = await api.guidedHandsPause();
      setGuidedHands(h);
      ok("Paused — click Resume when ready.");
    } catch (e) {
      err(errText(e));
    }
  };

  const resumeGuidedHands = async () => {
    try {
      const h = await api.guidedHandsResume();
      setGuidedHands(h);
      stopHandsPolling();
      handsTimer.current = window.setInterval(pollHands, 2000);
      ok("Resumed guided hands.");
    } catch (e) {
      err(errText(e));
    }
  };

  const bargeGuidedHands = async () => {
    try {
      const h = await api.guidedHandsBarge();
      setGuidedHands(h);
      ok("Your turn — click in the browser, then Resume.");
    } catch (e) {
      err(errText(e));
    }
  };

  const answerGuidedHands = async (
    qid: string,
    candidateIndex?: number,
    value?: string,
    skip = false,
  ) => {
    try {
      const h = await api.guidedHandsAnswer(qid, candidateIndex, value, skip);
      setGuidedHands(h);
      stopHandsPolling();
      handsTimer.current = window.setInterval(pollHands, 2000);
    } catch (e) {
      err(errText(e));
    }
  };

  const insertAskVisitor = async (insertAt: number) => {
    const label = window.prompt("What should the demo ask the visitor?", "Your phone number");
    if (!label?.trim()) return;
    try {
      const r = await api.guidedTaskPatch({
        insert_at: insertAt,
        new_step: {
          kind: "USER_INPUT",
          label: label.trim(),
          live_question: `Could you share ${label.trim().toLowerCase()}?`,
          spoken: label.trim(),
        },
      });
      setGuidedStatus(r.guided);
      ok("Ask-visitor beat added to the script.");
      void load();
    } catch (e) {
      err(errText(e));
    }
  };

  const poll = useCallback(async () => {
    try {
      const s = await api.recordStatus();
      const active = isRecording(s);
      setRecording(active);
      setRecPhase(s.phase || "");
      setSetupDiscarded(s.setup_discarded ?? 0);
      setCapturedSteps(s.steps ?? 0);
      setRecNarrating(!!s.narrate);
      setNarrationChunks(s.narration_chunks ?? 0);
      void refreshGuided();
      if (!active) {
        if (s.error?.trim()) err(s.error.trim());
        stopPolling();
        load();
      }
    } catch (e) {
      err(errText(e));
    }
  }, [err, load, refreshGuided]);

  const startRecord = async () => {
    if (!recUrl.trim()) return err("Enter your product start URL.");
    if (recSaveMode === "new" && !recName.trim()) return err("Flow name required.");
    if (recSaveMode === "update" && !recTargetFlowId.trim()) {
      return err("Pick a flow to update, or switch to Create new flow.");
    }
    const target = recordFlows.find((f) => f.flow_id === recTargetFlowId);
    const flowName =
      recSaveMode === "update" ? target?.name || recTargetFlowId : recName.trim();
    try {
      const r = await api.recordStart(recUrl.trim(), flowName, {
        narrate: recNarrate,
        save_mode: recSaveMode,
        target_flow_id: recSaveMode === "update" ? recTargetFlowId : undefined,
        target_flow_name: recSaveMode === "update" ? flowName : undefined,
      });
      setRecording(true);
      setRecPhase("setup");
      setSetupDiscarded(0);
      setCapturedSteps(0);
      setRecNarrating(!!r.narrate);
      setNarrationChunks(0);
      stopPolling();
      timer.current = window.setInterval(poll, 1500);
      ok(
        recSaveMode === "update"
          ? recNarrate
            ? `Setup — replacing ${flowName}. Click Narrate (top-right in browser) when ready.`
            : `Setup — replacing ${flowName}. Log in, then Start capturing.`
          : recNarrate
            ? "Setup — log in, then Start capturing. Click Narrate (top-right in browser) to speak while you click."
            : "Setup — log in and navigate to where the flow should begin, then Start capturing.",
      );
    } catch (e) {
      err(errText(e));
    }
  };

  const startCapture = async () => {
    try {
      const r = await api.recordCapture();
      setRecPhase(r.phase);
      setSetupDiscarded(r.setup_discarded);
      setCapturedSteps(r.steps);
      ok(
        `Capturing — ${r.setup_discarded} setup action${r.setup_discarded === 1 ? "" : "s"} ignored.`,
      );
    } catch (e) {
      err(errText(e));
    }
  };

  const stopRecord = async () => {
    try {
      const r = await api.recordStop();
      setRecording(false);
      setRecPhase("");
      stopPolling();
      await load();
      const flagged = r.flagged?.length ?? 0;
      const narrated = r.narrated_steps ?? 0;
      const base = r.error
        ? `Stopped with error: ${r.error}`
        : `Recorded ${r.steps} steps.`;
      const extra =
        (flagged > 0
          ? ` Dropped ${flagged} login step${flagged === 1 ? "" : "s"} (re-record after login if unexpected).`
          : "") +
        (narrated > 0
          ? ` Narration aligned to ${narrated} step${narrated === 1 ? "" : "s"}.`
          : "");
      ok(base + extra);
    } catch (e) {
      // Always leave the recording UI so Client is not stuck if merge/save 500s.
      setRecording(false);
      setRecPhase("");
      stopPolling();
      err(errText(e));
    }
  };

  const move = (i: number, delta: number) => {
    setRows((prev) => {
      if (!prev) return prev;
      const j = i + delta;
      if (j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next.map((f, n) => ({ ...f, order: n + 1 }));
    });
  };

  const patch = (i: number, key: keyof Flow, value: string) =>
    setRows((prev) => prev?.map((f, n) => (n === i ? { ...f, [key]: value } : f)) ?? prev);

  const save = async () => {
    if (!rows) return;
    try {
      const d = await api.putFlows(rows);
      const playlist = d.playlist ?? rows;
      setRows(playlist);
      applyPlaylist(playlist);
      ok("Flow playlist saved — open Site graph to review demo_playlist order.");
    } catch (e) {
      err(errText(e));
    }
  };

  const clearAllFlows = async () => {
    setConfirmClearAll(false);
    try {
      const d = await api.clearAllFlows();
      const playlist = d.playlist ?? [];
      setRows(playlist);
      applyPlaylist(playlist);
      setStepCount({});
      const explore = useExploreSession.getState();
      explore.setTargetFlowId("");
      explore.setTargetFlowName("");
      ok("Site graph and demo script cleared — run Auto-Explore to build a new walkthrough.");
    } catch (e) {
      err(errText(e));
    }
  };

  const confirmRemoveFlow = async () => {
    if (confirmDelete === null || !rows) return;
    const idx = confirmDelete;
    const row = rows[idx];
    setConfirmDelete(null);
    if (!row) return;

    // Empty / unsaved playlist row — local drop only.
    if (!row.flow_id?.trim()) {
      const next = rows
        .filter((_, n) => n !== idx)
        .map((f, n) => ({ ...f, order: n + 1 }));
      setRows(next);
      setPlaylist(next);
      ok("Row removed.");
      return;
    }

    try {
      const d = await api.deleteFlow(row.flow_id.trim(), row.page_id?.trim() || null);
      const playlist = d.playlist ?? [];
      setRows(playlist);
      applyPlaylist(playlist);
      const explore = useExploreSession.getState();
      if (explore.targetFlowId === row.flow_id.trim()) {
        explore.setTargetFlowId("");
        explore.setTargetFlowName("");
      }
      ok(`Deleted “${row.name || row.flow_id}” from the draft site graph.`);
    } catch (e) {
      err(errText(e));
    }
  };

  return (
    <motion.div variants={stagger()} initial="hidden" animate="show" className="grid gap-4">
      <Card>
        <CardTitle
          hint="Playlist order for guided demos. Top row runs first; demo continues down the list."
          right={
            <div className="flex gap-2">
              <Button
                variant="secondary"
                onClick={() => setConfirmClearAll(true)}
                disabled={!rows?.length && !Object.keys(stepCount).length}
              >
                <Trash2 size={14} /> Clear all
              </Button>
              <Button variant="secondary" onClick={() => setRows([...(rows ?? []), { name: "", page_id: "", flow_id: "" }])}>
                <Plus size={14} /> Add row
              </Button>
              <Button onClick={save} disabled={!rows}>
                <Save size={14} /> Save order
              </Button>
            </div>
          }
        >
          Demo flows
        </CardTitle>

        {!rows && <BarLoader label="Loading flows…" />}
        {rows?.length === 0 && <Empty>No flows — add a row or record one.</Empty>}

        <div className="space-y-1.5">
          <AnimatePresence initial={false}>
            {rows?.map((f, i) => {
              const flowId = f.flow_id?.trim() ?? "";
              const isUpdating = !!exploreUpdatingFlowId && flowId === exploreUpdatingFlowId;
              const rowDisabled = isUpdating;
              return (
              <motion.div
                key={`${f.flow_id || "row"}-${i}`}
                layout
                layoutId={`flow-${f.flow_id || i}`}
                transition={soft}
                exit={{ opacity: 0, x: -10 }}
                className={`grid grid-cols-[28px_auto_1fr_1fr_1fr_auto] items-start gap-2 rounded-lg border px-2 py-1.5 ${
                  rowDisabled ? "opacity-45 pointer-events-none" : ""
                }`}
                style={{ borderColor: "var(--line)" }}
              >
                <span className="text-center font-mono text-[0.72rem] text-[var(--muted)] flex flex-col justify-center">
                  <span>#{i + 1}</span>
                  {f.flow_id && stepCount[f.flow_id] !== undefined && (
                    <span className="text-[0.6rem] mt-0.5">{stepCount[f.flow_id]} steps</span>
                  )}
                </span>
                <span className="flex flex-col pt-5">
                  <Button variant="ghost" onClick={() => move(i, -1)} disabled={i === 0 || rowDisabled} className="h-5 px-1 py-0">
                    <ArrowUp size={11} />
                  </Button>
                  <Button variant="ghost" onClick={() => move(i, 1)} disabled={i === rows.length - 1 || rowDisabled} className="h-5 px-1 py-0">
                    <ArrowDown size={11} />
                  </Button>
                </span>
                <FlowFieldCell label="Name">
                  <Input
                    value={f.name ?? ""}
                    onChange={(v) => patch(i, "name", v)}
                    placeholder="e.g. Login tour"
                    disabled={rowDisabled}
                  />
                </FlowFieldCell>
                <FlowFieldCell label="Page ID">
                  <Input
                    value={f.page_id ?? ""}
                    onChange={(v) => patch(i, "page_id", v)}
                    placeholder="e.g. home"
                    disabled={rowDisabled}
                  />
                </FlowFieldCell>
                <FlowFieldCell label="Flow ID">
                  <Input
                    value={f.flow_id ?? ""}
                    onChange={(v) => patch(i, "flow_id", v)}
                    placeholder="e.g. login_flow"
                    disabled={rowDisabled}
                  />
                </FlowFieldCell>
                {isUpdating ? (
                  <div className="flex items-center self-center pt-4">
                  <FlowDraftLoader
                    pct={exploreDraftPct}
                    label={
                      exploreIsPersisting(exploreStatus)
                        ? "Saving explored steps…"
                        : "Exploring…"
                    }
                  />
                  </div>
                ) : (
                <div className="flex items-center self-center pt-4">
                <Button
                  variant="ghost"
                  onClick={() => setConfirmDelete(i)}
                  className="px-1.5 hover:text-red-500"
                  disabled={rowDisabled}
                >
                  <Trash2 size={13} />
                </Button>
                </div>
                )}
                {(f.verdict || f.purpose || (f.tags && f.tags.length > 0)) && (
                  <div className="col-span-6 flex flex-wrap items-center gap-2 px-1 pb-1 text-[0.68rem]">
                    {f.verdict && (
                      <span
                        className={
                          f.verdict === "ready"
                            ? "rounded px-1.5 py-0.5 font-medium text-emerald-700 bg-emerald-500/15"
                            : f.verdict === "broken"
                              ? "rounded px-1.5 py-0.5 font-medium text-red-700 bg-red-500/15"
                              : "rounded px-1.5 py-0.5 font-medium text-amber-700 bg-amber-500/15"
                        }
                      >
                        {f.verdict}
                      </span>
                    )}
                    {f.purpose && (
                      <span className="text-[var(--muted)] truncate max-w-[28rem]">
                        {f.purpose}
                      </span>
                    )}
                    {f.tags?.map((t) => (
                      <span
                        key={t}
                        className="rounded border px-1 py-0.5 text-[var(--muted)]"
                        style={{ borderColor: "var(--line)" }}
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </motion.div>
            );
            })}
            {showPendingNewRow && (
              <motion.div
                key="explore-pending-new-flow"
                layout
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                className="grid grid-cols-[28px_auto_1fr_1fr_1fr_auto] items-start gap-2 rounded-lg border border-dashed px-2 py-1.5 opacity-45"
                style={{ borderColor: "var(--line)" }}
              >
                <span className="text-center font-mono text-[0.72rem] text-[var(--muted)] pt-5">
                  …
                </span>
                <span />
                <FlowFieldCell label="Name">
                  <Input
                    value={pendingNewFlowName}
                    onChange={() => {}}
                    placeholder="e.g. Login tour"
                    disabled
                  />
                </FlowFieldCell>
                <FlowFieldCell label="Page ID">
                  <Input value="main" onChange={() => {}} placeholder="e.g. home" disabled />
                </FlowFieldCell>
                <FlowFieldCell label="Flow ID">
                  <Input
                    value={
                      exploreIsPersisting(exploreStatus)
                        ? "assigning…"
                        : "exploring…"
                    }
                    onChange={() => {}}
                    placeholder="e.g. login_flow"
                    disabled
                  />
                </FlowFieldCell>
                <div className="flex items-center self-center pt-4">
                <FlowDraftLoader
                  pct={exploreDraftPct}
                  label={
                    exploreIsPersisting(exploreStatus)
                      ? "Creating flow draft…"
                      : "Exploring product…"
                  }
                />
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </Card>
      {confirmDelete !== null && (
        <ConfirmDialog
          title="Delete this flow?"
          message={`Remove “${rows?.[confirmDelete]?.name || rows?.[confirmDelete]?.flow_id || "this flow"}” from the playlist and delete its steps from the draft site graph. Publish later to make the change live.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => {
            void confirmRemoveFlow();
          }}
          onCancel={() => setConfirmDelete(null)}
        />
      )}
      {confirmClearAll && (
        <ConfirmDialog
          title="Clear all flows?"
          message="Resets the draft site graph to a minimal empty shell and clears the demo script. Persona and product URL stay. Run Auto-Explore to build a new walkthrough."
          confirmLabel="Clear all"
          danger
          onConfirm={() => {
            void clearAllFlows();
          }}
          onCancel={() => setConfirmClearAll(false)}
        />
      )}

      <Card>
        <CardTitle
          hint="Opens a browser, records clicks, and saves a new flow or replaces an existing one in place."
          right={<StatusPill status={recording ? "recording" : "idle"} />}
        >
          Record a flow
        </CardTitle>
        <div className="grid gap-x-3 sm:grid-cols-2">
          <Field label="Save result as">
            <div
              className="flex rounded-lg border p-0.5"
              style={{ borderColor: "var(--line)" }}
            >
              <button
                type="button"
                disabled={recording}
                onClick={() => setRecSaveMode("new")}
                className={`flex-1 rounded-md px-2.5 py-1.5 text-[0.75rem] font-medium transition ${
                  recSaveMode === "new"
                    ? "bg-[var(--text)] text-[var(--bg)]"
                    : "text-[var(--muted)] hover:text-[var(--text)]"
                }`}
              >
                Create new flow
              </button>
              <button
                type="button"
                disabled={recording}
                onClick={() => setRecSaveMode("update")}
                className={`flex-1 rounded-md px-2.5 py-1.5 text-[0.75rem] font-medium transition ${
                  recSaveMode === "update"
                    ? "bg-[var(--text)] text-[var(--bg)]"
                    : "text-[var(--muted)] hover:text-[var(--text)]"
                }`}
              >
                Update existing
              </button>
            </div>
          </Field>
          {recSaveMode === "update" ? (
            <Field label="Flow to replace">
              <Select
                disabled={recording || recordFlows.length === 0}
                value={recTargetFlowId}
                onChange={setRecTargetFlowId}
                options={[
                  { value: "", label: "Select a flow…" },
                  ...recordFlows.map((f) => ({
                    value: f.flow_id,
                    label: `${f.name || f.flow_id}${
                      stepCount[f.flow_id] != null
                        ? ` · ${stepCount[f.flow_id]} steps`
                        : ""
                    }`,
                  })),
                ]}
              />
              {recordFlows.length === 0 && (
                <p className="mt-1 text-[0.68rem] text-[var(--muted)]">
                  No flows yet — create new first.
                </p>
              )}
            </Field>
          ) : (
            <Field label="Flow name">
              <Input
                value={recName}
                onChange={setRecName}
                disabled={recording}
                placeholder="e.g. onboarding tour"
              />
            </Field>
          )}
        </div>
        <Field label="Start URL">
          <Input
            value={recUrl}
            onChange={setRecUrl}
            disabled={recording}
            placeholder="https://your-product.example/"
          />
        </Field>
        <Switch
          checked={recNarrate}
          onChange={setRecNarrate}
          disabled={recording}
          label="Narrate while recording"
          description="Mic widget in browser (top-right): language, translate, record/pause/play. Script is cleaned and grammar-fixed when you stop."
        />
        <div className="flex flex-wrap gap-2">
          <Button onClick={startRecord} disabled={recording}>
            <Circle size={13} /> Start setup
          </Button>
          <Button
            onClick={startCapture}
            disabled={!recording || recPhase === "capturing"}
          >
            Start capturing this flow
          </Button>
          <Button variant="danger" onClick={stopRecord} disabled={!recording}>
            <Square size={13} /> Stop
          </Button>
        </div>
        {recording && (
          <div className="mt-4 rounded-xl border p-4 bg-black/[0.015] dark:bg-white/[0.015]" style={{ borderColor: "var(--line)" }}>
            <div className="flex items-center justify-between text-[0.78rem] font-medium tracking-tight mb-4">
              <div className={`flex items-center gap-2 ${recPhase === "setup" ? "text-blue-500" : "text-emerald-500"}`}>
                <div className="flex h-5 w-5 items-center justify-center rounded-full border-2 border-current">1</div>
                Setup
              </div>
              <div className="h-[2px] flex-1 mx-3 bg-black/10 dark:bg-white/10" />
              <div className={`flex items-center gap-2 ${recPhase === "capturing" ? "text-emerald-500" : "text-[var(--muted)]"}`}>
                <div className="flex h-5 w-5 items-center justify-center rounded-full border-2 border-current">2</div>
                Capturing
              </div>
            </div>
            <div className="space-y-2">
              <BarLoader
                label={
                  recPhase === "capturing"
                    ? `Recording — ${capturedSteps} step${capturedSteps === 1 ? "" : "s"} captured`
                    : `Setup — ${setupDiscarded} action${setupDiscarded === 1 ? "" : "s"} ignored`
                }
              />
              <p className="text-[0.72rem] text-[var(--muted)] leading-relaxed">
                {recPhase === "capturing"
                  ? recNarrating
                    ? "Only actions from this point are saved. Click Narrate in the browser (top-right) to record your voice."
                    : recSaveMode === "update"
                      ? "New recording replaces the selected flow from this point."
                      : "Only actions from this point are saved as the flow."
                  : recNarrating
                    ? "Log in and get to the starting screen. Use the Narrate widget in the browser when you begin capturing."
                    : recSaveMode === "update"
                      ? "Log in and navigate to where the new walkthrough should begin. Old steps are discarded on stop."
                      : "Log in and get to the flow’s starting screen. Nothing is saved yet."}
              </p>
              {recNarrating && narrationChunks > 0 && (
                <p className="text-[0.72rem] text-emerald-600 dark:text-emerald-400">
                  Voice captured — {narrationChunks} audio chunk{narrationChunks === 1 ? "" : "s"}.
                </p>
              )}
            </div>
          </div>
        )}
      </Card>

      <Card>
        <CardTitle
          hint="Describe the full demo in plain language. Navigator plans sections, creates soft stub flows, then helps click during recording."
          right={
            guidedStatus?.has_plan ? (
              <StatusPill status="idle" label={`${guidedStatus.percent_bound ?? 0}% bound`} />
            ) : undefined
          }
        >
          Guided Agent
        </CardTitle>
        <Field label="Agent task">
          <textarea
            value={agentTask}
            onChange={(e) => setAgentTask(e.target.value)}
            disabled={guidedPlanning || recording}
            placeholder="e.g. Ask for phone number, create a demo tag, add contact, open pipeline view…"
            rows={3}
            className="w-full resize-y rounded-lg border bg-transparent px-3 py-2 text-[0.8rem] leading-relaxed outline-none focus:ring-1 focus:ring-[var(--accent)]"
            style={{ borderColor: "var(--line)" }}
          />
        </Field>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button onClick={() => void runGuidedPlan()} disabled={guidedPlanning || recording}>
            {guidedPlanning ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Wand2 size={13} />
            )}{" "}
            Run task
          </Button>
          <Button
            variant="secondary"
            onClick={() => void startGuidedHands()}
            disabled={!recording || recPhase !== "capturing" || !guidedStatus?.has_plan}
          >
            Start guided hands
          </Button>
          {guidedHands?.active && (
            <>
              <Button
                variant="secondary"
                onClick={() => void pauseGuidedHands()}
                disabled={guidedHands.client_paused || guidedHands.phase === "paused"}
              >
                Pause
              </Button>
              <Button
                variant="secondary"
                onClick={() => void resumeGuidedHands()}
                disabled={
                  !guidedHands.client_paused &&
                  !guidedHands.barged &&
                  guidedHands.phase !== "paused" &&
                  guidedHands.phase !== "barged"
                }
              >
                Resume
              </Button>
              <Button
                variant="secondary"
                onClick={() => void bargeGuidedHands()}
                disabled={guidedHands.barged}
              >
                Take over
              </Button>
              <Button variant="danger" onClick={() => void stopGuidedHands()}>
                <Square size={13} /> Stop hands
              </Button>
            </>
          )}
        </div>
        {guidedStatus?.has_plan && (
          <div
            className="mt-4 grid gap-4 lg:grid-cols-2"
          >
            <div
              className="rounded-xl border p-4 bg-black/[0.015] dark:bg-white/[0.015]"
              style={{ borderColor: "var(--line)" }}
            >
              <div className="flex flex-wrap items-center justify-between gap-2 text-[0.78rem]">
                <span className="font-medium tracking-tight">Live drive</span>
                <StatusPill
                  status="idle"
                  label={
                    guidedHands?.active
                      ? guidedHands.phase || "active"
                      : `${guidedStatus.percent_bound ?? 0}% bound`
                  }
                />
              </div>
              <p className="mt-2 text-[0.72rem] text-[var(--muted)]">
                Recorder browser is the live canvas. Start capturing, then Start guided hands.
                Pause / Take over when stuck; Resume continues from the current step.
              </p>
              {guidedHands?.active && (
                <div className="mt-3">
                  <BarLoader
                    label={
                      guidedHands.phase === "awaiting_input"
                        ? "Waiting for you"
                        : guidedHands.phase === "barged"
                          ? "Your clicks"
                          : guidedHands.phase === "paused"
                            ? "Paused"
                            : `Hands — ${guidedHands.progress?.steps_done ?? 0}/${guidedHands.progress?.steps_total ?? 0} steps`
                    }
                  />
                  {guidedHands.current_step && (
                    <p className="mt-2 text-[0.72rem] text-[var(--muted)]">
                      Now: {guidedHands.current_flow} → {guidedHands.current_step}
                    </p>
                  )}
                  {guidedHands.question && (
                    <div className="mt-3 rounded-lg border border-amber-500/50 p-3">
                      <p className="text-[0.78rem] font-medium">{guidedHands.question.prompt}</p>
                      {guidedHands.question.kind === "user_input" ? (
                        <div className="mt-2 flex flex-wrap gap-2">
                          <Button
                            variant="secondary"
                            onClick={() =>
                              void answerGuidedHands(guidedHands.question!.qid, undefined, undefined, true)
                            }
                          >
                            Mark checkpoint (no fill)
                          </Button>
                        </div>
                      ) : (
                        <div className="mt-2 flex flex-wrap gap-2">
                          {(guidedHands.question.candidates ?? []).map((c) => (
                            <Button
                              key={c.index}
                              variant="secondary"
                              onClick={() =>
                                void answerGuidedHands(guidedHands.question!.qid, c.index)
                              }
                            >
                              {c.label || c.tag || `Option ${c.index + 1}`}
                            </Button>
                          ))}
                        </div>
                      )}
                      <p className="mt-2 text-[0.68rem] text-[var(--muted)]">
                        Or click the control in the browser — the recorder captures it.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
            <div
              className="rounded-xl border p-4 bg-black/[0.015] dark:bg-white/[0.015]"
              style={{ borderColor: "var(--line)" }}
            >
              <div className="flex flex-wrap items-center justify-between gap-2 text-[0.78rem]">
                <span className="font-medium tracking-tight">Script</span>
                <span className="font-mono tabular-nums text-[var(--muted)]">
                  {guidedStatus.progress?.steps_bound ?? 0}/
                  {guidedStatus.progress?.steps_total ?? 0} bound
                </span>
              </div>
              <div
                className="mt-2 h-2 overflow-hidden rounded-full"
                style={{ background: "color-mix(in oklab, var(--line) 80%, transparent)" }}
              >
                <div
                  className="h-full rounded-full bg-[var(--accent)] transition-all"
                  style={{ width: `${guidedStatus.percent_bound ?? 0}%` }}
                />
              </div>
              <ul className="mt-3 max-h-56 space-y-1 overflow-y-auto text-[0.72rem] text-[var(--muted)]">
                {((guidedStatus.flows ?? [])[0]?.step_list ?? []).map((s, i) => (
                  <li
                    key={`${s.alias}-${i}`}
                    className="flex items-center justify-between gap-2 rounded-md px-1 py-0.5 hover:bg-black/[0.03] dark:hover:bg-white/[0.03]"
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData("text/guided-insert", String(i));
                    }}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => {
                      e.preventDefault();
                      void insertAskVisitor(i);
                    }}
                  >
                    <span>
                      <span className="font-mono text-[0.62rem] uppercase text-[var(--muted)]">
                        {s.kind === "USER_INPUT" ? "ask" : "act"}
                      </span>{" "}
                      {i + 1}. {s.label}
                    </span>
                    <button
                      type="button"
                      className="shrink-0 text-[0.65rem] text-[var(--accent)] underline-offset-2 hover:underline"
                      onClick={() => void insertAskVisitor(i + 1)}
                    >
                      + Ask
                    </button>
                  </li>
                ))}
              </ul>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  variant="secondary"
                  onClick={() =>
                    void insertAskVisitor(guidedStatus.progress?.steps_total ?? 0)
                  }
                >
                  <Plus size={13} /> Ask visitor at end
                </Button>
              </div>
              <p className="mt-2 text-[0.72rem] text-[var(--muted)]">
                One flow. Use <strong>Update existing</strong> with flow{" "}
                <span className="font-mono">
                  {(guidedStatus.flows ?? [])[0]?.flow_id ?? "…"}
                </span>{" "}
                so the live demo walks real clicks.
              </p>
            </div>
          </div>
        )}
      </Card>

      <AutoExplore onFinished={load} />
    </motion.div>
  );
}

/** The second flow-creation path. Same review gate, no human walkthrough. */
function ExploreMeter({
  running,
  elapsedS,
  progressPct,
  steps,
  pages,
  maxPages,
}: {
  running: boolean;
  elapsedS: number;
  progressPct: number;
  steps: number;
  maxSteps?: number;
  pages: number;
  maxPages: number;
}) {
  const pct = Math.min(100, Math.max(0, Math.round(progressPct)));
  const radius = 34;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - pct / 100);

  return (
    <div
      className="mt-4 grid gap-3 rounded-xl border p-4 sm:grid-cols-[auto_1fr]"
      style={{
        borderColor: "var(--line)",
        background:
          "linear-gradient(135deg, color-mix(in oklab, var(--accent) 8%, transparent), transparent 70%)",
      }}
    >
      <div className="relative mx-auto flex h-[88px] w-[88px] items-center justify-center">
        <svg width="88" height="88" viewBox="0 0 88 88" className="-rotate-90">
          <circle
            cx="44"
            cy="44"
            r={radius}
            fill="none"
            stroke="var(--line)"
            strokeWidth="7"
          />
          <motion.circle
            cx="44"
            cy="44"
            r={radius}
            fill="none"
            stroke="var(--accent)"
            strokeWidth="7"
            strokeLinecap="round"
            strokeDasharray={circumference}
            animate={{ strokeDashoffset: offset }}
            transition={{ type: "spring", stiffness: 90, damping: 18 }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-[1.15rem] font-semibold tracking-tight tabular-nums">
            {pct}%
          </span>
          <span className="text-[0.62rem] uppercase tracking-[0.08em] text-[var(--muted)]">
            explored
          </span>
        </div>
      </div>

      <div className="flex min-w-0 flex-col justify-center gap-3">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="flex items-center gap-1.5 text-[0.7rem] font-medium uppercase tracking-[0.06em] text-[var(--muted)]">
              <Clock size={12} />
              Time elapsed
            </p>
            <p className="mt-0.5 font-mono text-[1.35rem] font-semibold tracking-tight tabular-nums">
              {formatExploreElapsed(elapsedS)}
              {running && (
                <span className="ml-2 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)] align-middle" />
              )}
            </p>
          </div>
          <p className="text-[0.7rem] text-[var(--muted)]">
            {running ? "Live budget progress" : "Final coverage"}
          </p>
        </div>

        <div>
          <div className="mb-1 flex justify-between text-[0.68rem] text-[var(--muted)]">
            <span>
              {pages}/{maxPages} pages in demo
            </span>
            <span>
              {steps} demo step{steps === 1 ? "" : "s"}
            </span>
          </div>
          <div
            className="h-2 overflow-hidden rounded-full"
            style={{ background: "color-mix(in oklab, var(--line) 80%, transparent)" }}
          >
            <motion.div
              className="h-full rounded-full bg-[var(--accent)]"
              initial={false}
              animate={{ width: `${pct}%` }}
              transition={{ type: "spring", stiffness: 90, damping: 18 }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function ExplorePlanBanner({
  saveMode,
  flowName,
  flowId,
  baseUrl,
  hasCredentials,
  phase,
}: {
  saveMode: "new" | "update";
  flowName: string;
  flowId: string;
  baseUrl: string;
  hasCredentials?: boolean;
  phase?: string;
}) {
  const modeLabel =
    saveMode === "update"
      ? `Update existing · ${flowName || flowId || "selected flow"}`
      : "Create new flow · unpublished draft";

  return (
    <div
      className="mb-3 rounded-lg border px-3 py-2.5"
      style={{
        borderColor: "var(--line)",
        background: "color-mix(in oklab, var(--accent) 6%, transparent)",
      }}
    >
      <p className="text-[0.7rem] font-medium uppercase tracking-[0.06em] text-[var(--muted)]">
        Explore options
      </p>
      <ul className="mt-1.5 space-y-1 text-[0.78rem] leading-snug">
        <li>
          <span className="text-[var(--muted)]">Save as · </span>
          <span className="font-medium">{modeLabel}</span>
          {saveMode === "update" && flowId && (
            <span className="ml-1 font-mono text-[0.68rem] text-[var(--muted)]">
              ({flowId})
            </span>
          )}
        </li>
        <li>
          <span className="text-[var(--muted)]">URL · </span>
          <span className="break-all font-mono text-[0.72rem]">
            {baseUrl.trim() || "Product Login URL (default)"}
          </span>
        </li>
        <li>
          <span className="text-[var(--muted)]">Login · </span>
          {hasCredentials ? "stored Product Login" : "signed-out pages only"}
        </li>
        {phase && (
          <li>
            <span className="text-[var(--muted)]">Phase · </span>
            <span className="font-medium">{phase}</span>
          </li>
        )}
      </ul>
    </div>
  );
}

function AutoExplore({ onFinished }: { onFinished: () => void }) {
  const { ok, err } = useUi();
  const status = useExploreSession((s) => s.status);
  const events = useExploreSession((s) => s.events);
  const question = useExploreSession((s) => s.question);
  const answer = useExploreSession((s) => s.answer);
  const baseUrl = useExploreSession((s) => s.baseUrl);
  const saveMode = useExploreSession((s) => s.saveMode);
  const targetFlowId = useExploreSession((s) => s.targetFlowId);
  const targetFlowName = useExploreSession((s) => s.targetFlowName);
  const exploreElapsed = useExploreElapsed();
  const showMeter = useExploreSession((s) => s.showMeter);
  const setBaseUrl = useExploreSession((s) => s.setBaseUrl);
  const setAnswer = useExploreSession((s) => s.setAnswer);
  const setSaveMode = useExploreSession((s) => s.setSaveMode);
  const setTargetFlowId = useExploreSession((s) => s.setTargetFlowId);
  const setTargetFlowName = useExploreSession((s) => s.setTargetFlowName);
  const newFlowName = useExploreSession((s) => s.newFlowName);
  const focusHint = useExploreSession((s) => s.focusHint);
  const setNewFlowName = useExploreSession((s) => s.setNewFlowName);
  const setFocusHint = useExploreSession((s) => s.setFocusHint);
  const setOnFlowDrafted = useExploreSession((s) => s.setOnFlowDrafted);
  const hydrate = useExploreSession((s) => s.hydrate);
  const syncProductUrl = useExploreSession((s) => s.syncProductUrl);
  const startExplore = useExploreSession((s) => s.start);
  const stopExplore = useExploreSession((s) => s.stop);
  const replyExplore = useExploreSession((s) => s.reply);
  const dismissResult = useExploreSession((s) => s.dismissResult);
  const logEnd = useRef<HTMLDivElement | null>(null);
  const logScrollRef = useRef<HTMLDivElement | null>(null);
  const pinnedToBottom = useRef(true);
  const flows = useProductData((s) => s.playlist).filter((f) => !!f.flow_id?.trim());
  const epoch = useProductData((s) => s.epoch);

  useEffect(() => {
    setOnFlowDrafted(onFinished);
    return () => setOnFlowDrafted(null);
  }, [onFinished, setOnFlowDrafted]);

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  useEffect(() => {
    void syncProductUrl();
  }, [epoch, syncProductUrl]);

  useEffect(() => {
    if (!targetFlowId) return;
    if (flows.some((f) => f.flow_id === targetFlowId)) return;
    setTargetFlowId("");
    setTargetFlowName("");
  }, [flows, targetFlowId, setTargetFlowId, setTargetFlowName]);

  useEffect(() => {
    if (!pinnedToBottom.current) return;
    const el = logScrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [events.length]);

  const onLogScroll = () => {
    const el = logScrollRef.current;
    if (!el) return;
    pinnedToBottom.current =
      el.scrollHeight - el.scrollTop - el.clientHeight <= 24;
  };

  const start = async () => {
    try {
      pinnedToBottom.current = true;
      const picked = flows.find((f) => f.flow_id === targetFlowId);
      await startExplore({ targetFlowName: picked?.name });
      ok(
        saveMode === "update"
          ? "Exploring — will overwrite the selected flow draft when done."
          : "Exploring — will create a new flow draft when done.",
      );
    } catch (e) {
      err(errText(e));
    }
  };

  const stop = async () => {
    try {
      await stopExplore();
      ok("Stopping — the steps found so far are saved as a draft.");
    } catch (e) {
      err(errText(e));
    }
  };

  const reply = async (skip: boolean) => {
    try {
      await replyExplore(skip);
    } catch (e) {
      err(errText(e));
    }
  };

  const running = exploreIsLive({ status, showMeter });
  const finished = exploreIsTerminal(status.phase);
  const flagged = status.flagged ?? [];
  const visitedPaths = status.visited_paths ?? [];
  const fieldDecisions = status.field_decisions ?? [];
  const liveInputFields = fieldDecisions.filter(
    (d) => d.classification === "business_specific",
  );
  const progressPct = exploreDraftProgressPct(status);
  const maxPages = status.budget?.max_pages ?? 25;
  const planMode = (status.save_mode === "update" ? "update" : saveMode) as
    | "new"
    | "update";
  const planFlowId = status.target_flow_id || targetFlowId;
  const planFlowName =
    status.target_flow_name ||
    status.new_flow_name ||
    targetFlowName ||
    (planMode === "new" ? newFlowName : "") ||
    planFlowId;
  const resultFlowId = status.flow_id || (planMode === "update" ? planFlowId : "");
  const resultFlowName =
    status.target_flow_name ||
    status.new_flow_name ||
    newFlowName ||
    targetFlowName ||
    flows.find((f) => f.flow_id === resultFlowId)?.name ||
    resultFlowId;
  const logLines = events.filter(
    (e) =>
      e.type === "log" ||
      e.type === "flagged" ||
      e.type === "field" ||
      e.type === "explored" ||
      e.type === "repair",
  );
  const exploreErrors = (() => {
    const msgs: string[] = [];
    if (status.error?.trim()) msgs.push(status.error.trim());
    for (const e of events) {
      if (e.type === "error" && e.msg) msgs.push(String(e.msg).trim());
    }
    return [...new Set(msgs.filter(Boolean))];
  })();
  const exploreWarnings = (() => {
    const benign =
      /repairs exhausted|no answer for .* within|skipping field|off-surface URL/i;
    const msgs: string[] = [];
    for (const e of events) {
      if (e.type === "log" && e.level === "warn" && e.msg) {
        const m = String(e.msg).trim();
        if (m && !benign.test(m)) msgs.push(m);
      }
    }
    const softBenign = events
      .filter(
        (e) =>
          e.type === "log" &&
          e.level === "warn" &&
          e.msg &&
          benign.test(String(e.msg)),
      )
      .map((e) => String(e.msg).trim());
    if (softBenign.length > 0 && exploreErrors.length === 0) {
      msgs.push(
        `${softBenign.length} step(s) needed extra retries (normal on complex UIs)`,
      );
    }
    return [...new Set(msgs.filter(Boolean))];
  })();
  const hasResult =
    finished &&
    (showMeter ||
      (status.steps ?? 0) > 0 ||
      !!status.flow_id ||
      logLines.length > 0 ||
      visitedPaths.length > 0 ||
      exploreErrors.length > 0 ||
      exploreWarnings.length > 0);

  const pillStatus = running
    ? question
      ? "starting"
      : "running"
    : finished
      ? status.phase === "failed"
        ? "failed"
        : "finished"
      : "idle";
  const pillLabel = running
    ? question
      ? "Waiting on you"
      : undefined
    : finished
      ? status.phase === "stopped"
        ? "Stopped"
        : status.phase === "failed"
          ? "Failed"
          : "Completed"
      : undefined;

  return (
    <Card>
      <CardTitle
        hint="Explores your product, maps pages, then drafts a clean demo walkthrough (one step per new page — no backtrack noise). Nothing is activated until you review it."
        right={
          <StatusPill status={pillStatus} label={pillLabel} />
        }
      >
        Auto-Explore &amp; Generate Flow
      </CardTitle>

      <div className="grid gap-x-3 sm:grid-cols-2">
        <Field label="Product URL">
          <Input
            value={baseUrl}
            onChange={setBaseUrl}
            placeholder="Filled from Product Login / Domain — editable"
          />
        </Field>
        <Field label="Credentials">
          <p className="text-[0.72rem] leading-relaxed text-[var(--muted)] py-1.5">
            {status.has_credentials
              ? "Using your saved Product Login. No credentials are entered here."
              : "No Product Login saved — only your signed-out pages will be explored."}
          </p>
        </Field>
      </div>

      <div className="mt-1 grid gap-3 sm:grid-cols-2">
        <Field label="Save result as">
          <div
            className="flex rounded-lg border p-0.5"
            style={{ borderColor: "var(--line)" }}
          >
            <button
              type="button"
              disabled={running}
              onClick={() => setSaveMode("new")}
              className={`flex-1 rounded-md px-2.5 py-1.5 text-[0.75rem] font-medium transition ${
                saveMode === "new"
                  ? "bg-[var(--text)] text-[var(--bg)]"
                  : "text-[var(--muted)] hover:text-[var(--text)]"
              }`}
            >
              Create new flow
            </button>
            <button
              type="button"
              disabled={running}
              onClick={() => setSaveMode("update")}
              className={`flex-1 rounded-md px-2.5 py-1.5 text-[0.75rem] font-medium transition ${
                saveMode === "update"
                  ? "bg-[var(--text)] text-[var(--bg)]"
                  : "text-[var(--muted)] hover:text-[var(--text)]"
              }`}
            >
              Update existing
            </button>
          </div>
        </Field>
        {saveMode === "update" ? (
          <Field label="Flow to overwrite">
            <Select
              disabled={running || flows.length === 0}
              value={targetFlowId}
              onChange={(id) => {
                setTargetFlowId(id);
                const hit = flows.find((f) => f.flow_id === id);
                setTargetFlowName(hit?.name || "");
              }}
              options={[
                { value: "", label: "Select a flow…" },
                ...flows.map((f) => ({
                  value: f.flow_id,
                  label: f.name || f.flow_id,
                })),
              ]}
            />
            {flows.length === 0 && (
              <p className="mt-1 text-[0.68rem] text-[var(--muted)]">
                No flows yet — create new first.
              </p>
            )}
          </Field>
        ) : (
          <Field label="New flow name">
            <Input
              value={newFlowName}
              onChange={setNewFlowName}
              disabled={running}
              placeholder="e.g. Inbox walkthrough, Billing demo"
            />
          </Field>
        )}
      </div>

      <div className="mt-1 grid gap-3 sm:grid-cols-2">
        <Field label="Focus area (optional)">
          <Input
            value={focusHint}
            onChange={setFocusHint}
            disabled={running}
            placeholder="Tab or feature name — e.g. Meta accounts, Campaigns"
          />
        </Field>
        <Field label="Demo draft rules">
          <p className="text-[0.72rem] leading-relaxed text-[var(--muted)] py-1.5">
            Only first visit per page becomes a demo step. Extra clicks still map
            the site but stay out of the walkthrough. Business-specific form fields
            are saved as live-input beats (demo asks your visitor).
          </p>
        </Field>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button onClick={start} disabled={running}>
          <Compass size={13} /> Start exploring
        </Button>
        <Button variant="danger" onClick={stop} disabled={!running}>
          <Square size={13} /> Stop exploring
        </Button>
      </div>

      {running && (
        <>
          <div className="mt-4">
            <ExplorePlanBanner
              saveMode={planMode}
              flowName={planFlowName}
              flowId={planFlowId}
              baseUrl={baseUrl}
              hasCredentials={status.has_credentials}
              phase={status.phase}
            />
          </div>
          <ExploreMeter
            running={running}
            elapsedS={exploreElapsed}
            progressPct={progressPct}
            steps={status.steps ?? 0}
            pages={status.visited ?? 0}
            maxPages={maxPages}
          />

          {question && (
            <div
              className="mt-4 rounded-xl border-2 border-amber-500/60 p-4"
              style={{ background: "rgb(245 158 11 / 0.06)" }}
            >
              <p className="text-[0.78rem] font-medium tracking-tight">Waiting on your input</p>
              <p className="mt-1 text-[0.8rem]">{question.prompt}</p>
              <p className="mt-0.5 text-[0.7rem] text-[var(--muted)]">
                Field <code>{question.alias}</code>
                {question.context?.url ? ` on ${question.context.url}` : ""}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Input value={answer} onChange={setAnswer} placeholder="Your answer" />
                <Button onClick={() => reply(false)} disabled={!answer.trim()}>
                  Answer &amp; resume
                </Button>
                <Button variant="secondary" onClick={() => reply(true)}>
                  Skip this field
                </Button>
              </div>
            </div>
          )}

          <ExploreWatch live={running} />

          <div
            className="mt-4 rounded-xl border p-4 bg-black/[0.015] dark:bg-white/[0.015]"
            style={{ borderColor: "var(--line)" }}
          >
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <p className="text-[0.78rem] font-medium tracking-tight">Exploration log</p>
              <Button variant="danger" onClick={stop}>
                <Square size={12} /> Stop
              </Button>
            </div>
            <BarLoader
              label={`${status.phase ?? "exploring"} — ${
                planMode === "update"
                  ? `updating “${planFlowName || planFlowId}”`
                  : "creating new flow"
              }`}
            />
            {visitedPaths.length > 0 && (
              <div className="mt-3">
                <p className="text-[0.7rem] font-medium text-[var(--muted)]">Pages explored</p>
                <ul className="mt-1 space-y-0.5 font-mono text-[0.68rem] text-[var(--muted)]">
                  {visitedPaths.map((p) => (
                    <li key={p}>• {p}</li>
                  ))}
                </ul>
              </div>
            )}
            <div
              ref={logScrollRef}
              onScroll={onLogScroll}
              className="mt-3 max-h-72 overflow-y-auto space-y-0.5 font-mono text-[0.68rem] leading-relaxed"
            >
              {logLines.length === 0 && (
                <div className="text-[var(--muted)]">Waiting for explorer events…</div>
              )}
              {logLines.map((e, i) => (
                <div
                  key={i}
                  className={
                    e.type === "flagged"
                      ? "text-amber-600 dark:text-amber-400"
                      : e.type === "repair"
                        ? e.ok
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-violet-600 dark:text-violet-400"
                        : e.type === "explored"
                          ? "text-sky-600 dark:text-sky-400"
                          : e.level === "warn"
                            ? "text-red-500"
                            : "text-[var(--muted)]"
                  }
                >
                  {e.type === "flagged"
                    ? `skipped "${e.label}" — ${e.reason}`
                    : e.type === "field"
                      ? `filled ${e.alias} (${e.classification})`
                      : e.type === "repair"
                        ? e.ok
                          ? `repaired ${String(e.alias ?? "")} via ${
                              Array.isArray(e.tactics) ? e.tactics.join(" → ") : "ladder"
                            }`
                          : `repairing ${String(e.alias ?? "")} (${String(e.kind ?? "?")})`
                        : e.type === "explored"
                          ? `page ${String(e.path ?? e.url ?? "")} (${e.elements ?? "?"} controls)`
                          : String(e.msg ?? "")}
                </div>
              ))}
              <div ref={logEnd} />
            </div>
          </div>

          {flagged.length > 0 && (
            <div className="mt-4">
              <p className="flex items-center gap-1.5 text-[0.78rem] font-medium tracking-tight">
                <ShieldAlert size={14} className="text-amber-500" />
                Skipped — needs your review ({flagged.length})
              </p>
              <p className="mt-1 text-[0.7rem] text-[var(--muted)]">
                Safety gate blocked these. <strong>Allow</strong> lets the bot click
                on this explore; <strong>Dismiss</strong> hides without clicking.
              </p>
              <div className="mt-2 space-y-2">
                {flagged.map((f, i) => (
                  <FlaggedReviewRow
                    key={`${f.selector}-${f.label}-${i}`}
                    item={f}
                    live={running}
                    onDone={() => void useExploreSession.getState().refresh()}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {hasResult && !running && (
        <div
          className="mt-4 rounded-xl border p-4"
          style={{
            borderColor: "var(--line)",
            background:
              "linear-gradient(135deg, color-mix(in oklab, var(--accent) 8%, transparent), transparent 70%)",
          }}
        >
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <p className="text-[0.7rem] font-medium uppercase tracking-[0.06em] text-[var(--muted)]">
                {status.phase === "failed"
                  ? "Explore failed"
                  : status.phase === "stopped"
                    ? "Explore stopped"
                    : "Explore completed"}
              </p>
              <p className="mt-1 text-[0.95rem] font-semibold tracking-tight">
                {resultFlowId
                  ? `Draft flow · ${resultFlowName || resultFlowId}`
                  : (status.steps ?? 0) > 0
                    ? "Saving draft…"
                    : "No demo steps captured"}
              </p>
            </div>
            <Button variant="secondary" onClick={() => dismissResult()}>
              Clear
            </Button>
          </div>
          <ul className="mt-3 space-y-1 text-[0.78rem] leading-snug">
            {resultFlowId && (
              <li>
                <span className="text-[var(--muted)]">Flow id · </span>
                <span className="font-mono text-[0.72rem]">{resultFlowId}</span>
                {status.revision != null && (
                  <span className="text-[var(--muted)]">
                    {" "}
                    · draft revision {status.revision}
                  </span>
                )}
              </li>
            )}
            <li>
              <span className="text-[var(--muted)]">Demo steps · </span>
              <span className="font-medium">{status.steps ?? 0}</span>
              <span className="text-[var(--muted)]">
                {" "}
                · {status.visited ?? visitedPaths.length} pages ·{" "}
                {formatExploreElapsed(exploreElapsed)} elapsed
              </span>
            </li>
            <li>
              <span className="text-[var(--muted)]">Save mode · </span>
              {planMode === "update"
                ? `updated existing “${planFlowName || planFlowId}”`
                : "new unpublished draft"}
            </li>
            {status.stop_reason && (
              <li>
                <span className="text-[var(--muted)]">Ended · </span>
                <span className="font-medium">
                  {status.stop_reason.replace(
                    /^dead end at blank$/i,
                    "finished mapping — nothing new to try (recovered from blank page)",
                  )}
                </span>
              </li>
            )}
            {flagged.length > 0 && (
              <li className="text-amber-700 dark:text-amber-400">
                {flagged.length} control
                {flagged.length === 1 ? "" : "s"} skipped by safety gate (not in
                demo)
              </li>
            )}
            {liveInputFields.length > 0 && (
              <li>
                <span className="text-[var(--muted)]">Live input fields · </span>
                <span className="font-medium">{liveInputFields.length}</span>
                <span className="text-[var(--muted)]">
                  {" "}
                  — demo will ask your visitor on these
                </span>
              </li>
            )}
          </ul>
          {liveInputFields.length > 0 && (
            <div
              className="mt-3 rounded-lg border px-3 py-2.5"
              style={{ borderColor: "var(--line)", background: "var(--bg)" }}
            >
              <p className="text-[0.72rem] font-medium tracking-tight">
                Fields marked for live visitor input
              </p>
              <ul className="mt-2 space-y-1 text-[0.72rem] text-[var(--muted)]">
                {liveInputFields.map((f) => (
                  <li key={`${f.alias}-${f.label}`}>
                    <span className="font-medium text-[var(--text)]">{f.label}</span>
                    {f.answered_by === "client" && f.value ? (
                      <span> — example during explore: {f.value}</span>
                    ) : f.answered_by?.startsWith("skipped") ? (
                      <span> — skipped during explore (not in demo)</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {exploreWarnings.length > 0 && exploreErrors.length === 0 && (
            <div
              className="mt-3 rounded-lg border border-amber-500/40 px-3 py-2.5"
              style={{ background: "rgb(245 158 11 / 0.06)" }}
            >
              <p className="text-[0.72rem] font-medium text-amber-800 dark:text-amber-300">
                Notes from explore
              </p>
              <ul className="mt-1.5 max-h-36 space-y-1 overflow-y-auto text-[0.72rem] leading-snug text-amber-900 dark:text-amber-200">
                {exploreWarnings.map((msg) => (
                  <li key={msg}>• {msg}</li>
                ))}
              </ul>
            </div>
          )}
          {exploreErrors.length > 0 && (
            <div
              className="mt-3 rounded-lg border border-red-500/40 px-3 py-2.5"
              style={{ background: "rgb(239 68 68 / 0.06)" }}
            >
              <p className="text-[0.72rem] font-medium text-red-600 dark:text-red-400">
                Errors during explore ({exploreErrors.length})
              </p>
              <ul className="mt-1.5 max-h-36 space-y-1 overflow-y-auto text-[0.72rem] leading-snug text-red-700 dark:text-red-300">
                {exploreErrors.map((msg) => (
                  <li key={msg}>• {msg}</li>
                ))}
              </ul>
            </div>
          )}
          {visitedPaths.length > 0 && (
            <div className="mt-3">
              <p className="text-[0.7rem] font-medium text-[var(--muted)]">Pages covered</p>
              <ul className="mt-1 max-h-28 overflow-y-auto space-y-0.5 font-mono text-[0.68rem] text-[var(--muted)]">
                {visitedPaths.map((p) => (
                  <li key={p}>• {p}</li>
                ))}
              </ul>
            </div>
          )}
          <p className="mt-3 text-[0.7rem] text-[var(--muted)]">
            Unpublished draft — review in the playlist above, then publish the site
            graph when ready for visitors
            {status.repairs_used
              ? ` (${status.repairs_used} selector repair${status.repairs_used === 1 ? "" : "s"} only go live after Publish)`
              : ""}
            .
          </p>
        </div>
      )}
    </Card>
  );
}

function humanizeFlagReason(reason: string, source: string): string {
  const r = (reason || "").trim();
  const m = /^keyword:(.+)$/i.exec(r);
  if (m) {
    return `Label/text contains “${m[1]}” — clicking may send, change, or delete real data.`;
  }
  if (source === "llm" || /destructive|mutat/i.test(r)) {
    return r || "Model judged this control may mutate real data.";
  }
  if (source === "fail_closed") {
    return r || "Blocked because safety could not confirm this is read-only.";
  }
  return r || "Blocked as potentially unsafe.";
}

function FlaggedReviewRow({
  item,
  live,
  onDone,
}: {
  item: ExploreFlagged;
  live: boolean;
  onDone: () => void;
}) {
  const { ok, err } = useUi();
  const [busy, setBusy] = useState(false);

  const act = async (action: "allow" | "dismiss") => {
    setBusy(true);
    try {
      await api.exploreFlagged({
        action,
        selector: item.selector,
        label: item.label,
        element_key: item.element_key,
      });
      ok(
        action === "allow"
          ? `Allowed “${item.label}” — bot may click it on this explore.`
          : `Dismissed “${item.label}”.`,
      );
      onDone();
    } catch (e) {
      err(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="rounded-lg border px-3 py-2.5 text-[0.72rem]"
      style={{ borderColor: "var(--line)" }}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="font-medium tracking-tight">{item.label || item.selector}</p>
          <p className="mt-0.5 text-[var(--muted)]">
            {humanizeFlagReason(item.reason, item.source)}
          </p>
          {(item.url || item.selector) && (
            <p className="mt-1 font-mono text-[0.65rem] text-[var(--muted)] truncate">
              {item.url ? `${item.url}` : ""}
              {item.url && item.selector ? " · " : ""}
              {item.selector || ""}
            </p>
          )}
        </div>
          <div className="flex shrink-0 flex-wrap gap-1.5">
          <Button
            variant="secondary"
            disabled={busy || !live}
            onClick={() => void act("allow")}
          >
            Allow
          </Button>
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => void act("dismiss")}
          >
            Dismiss
          </Button>
        </div>
      </div>
      {!live && (
        <p className="mt-1.5 text-[0.65rem] text-[var(--muted)]">
          Allow needs an active explore — start again to let the bot click this.
        </p>
      )}
    </div>
  );
}
