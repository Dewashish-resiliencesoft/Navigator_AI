import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowDown, ArrowUp, Circle, Plus, Save, Square, Trash2 } from "lucide-react";
import { api, type Flow, type RecorderStatus } from "../lib/api";
import { soft, stagger } from "../lib/motion";
import {
  BarLoader,
  Button,
  Card,
  CardTitle,
  Empty,
  Field,
  Input,
  StatusPill,
  ConfirmDialog,
} from "../components/ui";
import { errText, useUi } from "../store";

const isRecording = (s: RecorderStatus) =>
  !!(s.recording || s.active || s.status === "recording");

export function Flows() {
  const { ok, err } = useUi();
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
      setRows(d.playlist ?? []);
      
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
  }, [err]);

  useEffect(() => {
    load();
  }, [load]);

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
      setRows(d.playlist ?? rows);
      ok("Flow playlist saved.");
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
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
        {confirmDelete !== null && (
          <ConfirmDialog
            title="Remove flow from playlist?"
            message={`Are you sure you want to remove "${rows?.[confirmDelete]?.name || "this flow"}" from the playlist? The flow definition will remain in the site graph.`}
            confirmLabel="Remove"
            danger
            onConfirm={() => {
              setRows(rows!.filter((_, n) => n !== confirmDelete));
              setConfirmDelete(null);
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
    </motion.div>
  );
}
