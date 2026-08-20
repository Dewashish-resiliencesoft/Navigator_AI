import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowDown,
  ArrowUp,
  Circle,
  Plus,
  Save,
  Square,
  Trash2,
} from "lucide-react";
import { api, type Flow, type RecorderStatus } from "../lib/api";
import { useProductData } from "../lib/productData";
import { soft, stagger } from "../lib/motion";
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
  const timer = useRef<number | null>(null);

  const recordFlows = (rows ?? []).filter((f) => !!f.flow_id?.trim());

  const load = useCallback(async () => {
    try {
      const [d, g] = await Promise.all([api.getFlows(), api.getSiteGraph()]);
      const playlist = d.playlist ?? [];
      setRows(playlist);
      // ponytail: setPlaylist only — applyPlaylist would re-bump epoch and loop load
      setPlaylist(playlist);

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
      if (!active) {
        if (s.error?.trim()) err(s.error.trim());
        stopPolling();
        load();
      }
    } catch (e) {
      err(errText(e));
    }
  }, [err, load]);

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
      ok("Site graph and demo script cleared — record a new flow to rebuild.");
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
      ok(`Deleted “${row.name || row.flow_id}” from the draft site graph.`);
    } catch (e) {
      err(errText(e));
    }
  };

  return (
    <motion.div variants={stagger()} initial="hidden" animate="show" className="grid gap-4">
      <Card dataCoach="flows-playlist">
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
            {rows?.map((f, i) => (
              <motion.div
                key={`${f.flow_id || "row"}-${i}`}
                layout
                layoutId={`flow-${f.flow_id || i}`}
                transition={soft}
                exit={{ opacity: 0, x: -10 }}
                className="grid grid-cols-[28px_auto_1fr_1fr_1fr_auto] items-start gap-2 rounded-lg border px-2 py-1.5"
                style={{ borderColor: "var(--line)" }}
              >
                <span className="text-center font-mono text-[0.72rem] text-[var(--muted)] flex flex-col justify-center">
                  <span>#{i + 1}</span>
                  {f.flow_id && stepCount[f.flow_id] !== undefined && (
                    <span className="text-[0.6rem] mt-0.5">{stepCount[f.flow_id]} steps</span>
                  )}
                </span>
                <span className="flex flex-col pt-5">
                  <Button variant="ghost" onClick={() => move(i, -1)} disabled={i === 0} className="h-5 px-1 py-0">
                    <ArrowUp size={11} />
                  </Button>
                  <Button variant="ghost" onClick={() => move(i, 1)} disabled={i === rows.length - 1} className="h-5 px-1 py-0">
                    <ArrowDown size={11} />
                  </Button>
                </span>
                <FlowFieldCell label="Name">
                  <Input
                    value={f.name ?? ""}
                    onChange={(v) => patch(i, "name", v)}
                    placeholder="e.g. Login tour"
                  />
                </FlowFieldCell>
                <FlowFieldCell label="Page ID">
                  <Input
                    value={f.page_id ?? ""}
                    onChange={(v) => patch(i, "page_id", v)}
                    placeholder="e.g. home"
                  />
                </FlowFieldCell>
                <FlowFieldCell label="Flow ID">
                  <Input
                    value={f.flow_id ?? ""}
                    onChange={(v) => patch(i, "flow_id", v)}
                    placeholder="e.g. login_flow"
                  />
                </FlowFieldCell>
                <div className="flex items-center self-center pt-4">
                <Button
                  variant="ghost"
                  onClick={() => setConfirmDelete(i)}
                  className="px-1.5 hover:text-red-500"
                >
                  <Trash2 size={13} />
                </Button>
                </div>
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
            ))}
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
          message="Resets the draft site graph to a minimal empty shell and clears the demo script. Persona and product URL stay. Record a new flow to rebuild."
          confirmLabel="Clear all"
          danger
          onConfirm={() => {
            void clearAllFlows();
          }}
          onCancel={() => setConfirmClearAll(false)}
        />
      )}

      <Card dataCoach="flows-record">
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
    </motion.div>
  );
}
