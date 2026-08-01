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
} from "../components/ui";
import { errText, useUi } from "../store";

const isRecording = (s: RecorderStatus) =>
  !!(s.recording || s.active || s.status === "recording");

export function Flows() {
  const { ok, err } = useUi();
  const [rows, setRows] = useState<Flow[] | null>(null);
  const [recording, setRecording] = useState(false);
  const [recName, setRecName] = useState("");
  const [recUrl, setRecUrl] = useState("");
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.getFlows();
      setRows(d.playlist ?? []);
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
      stopPolling();
      timer.current = window.setInterval(poll, 1500);
      ok("Recording — drive your product in the opened browser.");
    } catch (e) {
      err(errText(e));
    }
  };

  const stopRecord = async () => {
    try {
      const r = await api.recordStop();
      setRecording(false);
      stopPolling();
      await load();
      ok(r.error ? `Stopped with error: ${r.error}` : `Recorded ${r.steps} steps.`);
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
                <span className="text-center font-mono text-[0.72rem] text-[var(--muted)]">
                  {i + 1}
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
                  onClick={() => setRows(rows.filter((_, n) => n !== i))}
                  className="px-1.5"
                >
                  <Trash2 size={13} />
                </Button>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
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
        <div className="flex gap-2">
          <Button onClick={startRecord} disabled={recording}>
            <Circle size={13} /> Start record
          </Button>
          <Button variant="danger" onClick={stopRecord} disabled={!recording}>
            <Square size={13} /> Stop record
          </Button>
        </div>
        {recording && (
          <div className="mt-3">
            <BarLoader label="capturing steps" />
          </div>
        )}
      </Card>
    </motion.div>
  );
}
