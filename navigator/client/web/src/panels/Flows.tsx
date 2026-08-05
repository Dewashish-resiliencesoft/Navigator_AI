import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowDown,
  ArrowUp,
  Circle,
  Clock,
  Compass,
  Plus,
  Save,
  ShieldAlert,
  Square,
  Trash2,
} from "lucide-react";
import { api, type ExploreFlagged, type Flow, type RecorderStatus } from "../lib/api";
import {
  exploreIsLive,
  exploreIsTerminal,
  formatExploreElapsed,
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
  ConfirmDialog,
} from "../components/ui";
import { errText, useUi } from "../store";

const isRecording = (s: RecorderStatus) =>
  !!(s.recording || s.active || s.status === "recording");

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
  const [stepCount, setStepCount] = useState<Record<string, number>>({});
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const timer = useRef<number | null>(null);

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
      if (!active) {
        stopPolling();
        load();
      }
    } catch (e) {
      err(errText(e));
    }
  }, [err, load]);

  const startRecord = async () => {
    if (!recUrl.trim()) return err("Enter your product start URL.");
    if (!recName.trim()) return err("Flow name required.");
    try {
      await api.recordStart(recUrl.trim(), recName.trim());
      setRecording(true);
      setRecPhase("setup");
      setSetupDiscarded(0);
      setCapturedSteps(0);
      stopPolling();
      timer.current = window.setInterval(poll, 1500);
      ok("Setup — log in and navigate to where the flow should begin, then Start capturing.");
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
      const base = r.error
        ? `Stopped with error: ${r.error}`
        : `Recorded ${r.steps} steps.`;
      const extra =
        flagged > 0
          ? ` Dropped ${flagged} login step${flagged === 1 ? "" : "s"} (re-record after login if unexpected).`
          : "";
      ok(base + extra);
    } catch (e) {
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
      ok("Flow playlist saved.");
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
          hint="Playlist order for guided demos. The top row is the default walkthrough."
          right={
            <div className="flex gap-2">
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
                className="grid grid-cols-[28px_auto_1fr_1fr_1fr_auto] items-center gap-2 rounded-lg border px-2 py-1.5"
                style={{ borderColor: "var(--line)" }}
              >
                <span className="text-center font-mono text-[0.72rem] text-[var(--muted)] flex flex-col justify-center">
                  <span>#{i + 1}</span>
                  {f.flow_id && stepCount[f.flow_id] !== undefined && (
                    <span className="text-[0.6rem] mt-0.5">{stepCount[f.flow_id]} steps</span>
                  )}
                </span>
                <span className="flex flex-col">
                  <Button variant="ghost" onClick={() => move(i, -1)} disabled={i === 0} className="h-5 px-1 py-0">
                    <ArrowUp size={11} />
                  </Button>
                  <Button variant="ghost" onClick={() => move(i, 1)} disabled={i === rows.length - 1} className="h-5 px-1 py-0">
                    <ArrowDown size={11} />
                  </Button>
                </span>
                <Input value={f.name ?? ""} onChange={(v) => patch(i, "name", v)} placeholder="name" />
                <Input value={f.page_id ?? ""} onChange={(v) => patch(i, "page_id", v)} placeholder="page_id" />
                <Input value={f.flow_id ?? ""} onChange={(v) => patch(i, "flow_id", v)} placeholder="flow_id" />
                <Button
                  variant="ghost"
                  onClick={() => setConfirmDelete(i)}
                  className="px-1.5 hover:text-red-500"
                >
                  <Trash2 size={13} />
                </Button>
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
      </Card>

      <Card>
        <CardTitle
          hint="Opens a browser, records your clicks, and merges them into the site graph as a new flow."
          right={<StatusPill status={recording ? "recording" : "idle"} />}
        >
          Record a flow
        </CardTitle>
        <div className="grid gap-x-3 sm:grid-cols-2">
          <Field label="Flow name">
            <Input value={recName} onChange={setRecName} placeholder="e.g. onboarding tour" />
          </Field>
          <Field label="Start URL">
            <Input value={recUrl} onChange={setRecUrl} placeholder="https://your-product.example/" />
          </Field>
        </div>
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
                  ? "Only actions from this point are saved as the flow."
                  : "Log in and get to the flow’s starting screen. Nothing is saved yet."}
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
  const elapsedLocal = useExploreSession((s) => s.elapsedLocal);
  const showMeter = useExploreSession((s) => s.showMeter);
  const setBaseUrl = useExploreSession((s) => s.setBaseUrl);
  const setAnswer = useExploreSession((s) => s.setAnswer);
  const setSaveMode = useExploreSession((s) => s.setSaveMode);
  const setTargetFlowId = useExploreSession((s) => s.setTargetFlowId);
  const setTargetFlowName = useExploreSession((s) => s.setTargetFlowName);
  const setOnFlowDrafted = useExploreSession((s) => s.setOnFlowDrafted);
  const hydrate = useExploreSession((s) => s.hydrate);
  const syncProductUrl = useExploreSession((s) => s.syncProductUrl);
  const startExplore = useExploreSession((s) => s.start);
  const stopExplore = useExploreSession((s) => s.stop);
  const replyExplore = useExploreSession((s) => s.reply);
  const dismissResult = useExploreSession((s) => s.dismissResult);
  const logEnd = useRef<HTMLDivElement | null>(null);
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
    logEnd.current?.scrollIntoView({ block: "nearest" });
  }, [events.length]);

  const start = async () => {
    try {
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
  const progressPct = status.progress_pct ?? 0;
  const maxPages = status.budget?.max_pages ?? 25;
  const planMode = (status.save_mode === "update" ? "update" : saveMode) as
    | "new"
    | "update";
  const planFlowId = status.target_flow_id || targetFlowId;
  const planFlowName =
    status.target_flow_name || targetFlowName || planFlowId;
  const resultFlowId = status.flow_id || (planMode === "update" ? planFlowId : "");
  const resultFlowName =
    status.target_flow_name ||
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
      if (e.type === "log" && e.level === "warn" && e.msg) {
        msgs.push(String(e.msg).trim());
      }
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
      exploreErrors.length > 0);

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
          <Field label="Demo draft">
            <p className="text-[0.72rem] leading-relaxed text-[var(--muted)] py-1.5">
              Saves a new unpublished flow. Only first visit per page becomes a
              demo step — extra clicks still explore, not the walkthrough.
            </p>
          </Field>
        )}
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
            elapsedS={elapsedLocal}
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
            <div className="mt-3 max-h-72 overflow-y-auto space-y-0.5 font-mono text-[0.68rem] leading-relaxed">
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
                {formatExploreElapsed(elapsedLocal)} elapsed
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
                <span className="font-medium">{status.stop_reason}</span>
              </li>
            )}
            {flagged.length > 0 && (
              <li className="text-amber-700 dark:text-amber-400">
                {flagged.length} control
                {flagged.length === 1 ? "" : "s"} skipped by safety gate (not in
                demo)
              </li>
            )}
          </ul>
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
            graph when ready for visitors.
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
