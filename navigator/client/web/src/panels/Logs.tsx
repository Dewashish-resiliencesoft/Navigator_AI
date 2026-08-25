import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { ChevronDown, ChevronRight, PhoneOff } from "lucide-react";
import { api, DASHBOARD_DAYS, ApiError, type DecisionTrace, type DemoRun, type RunEvent } from "../lib/api";
import { demoIsLive, useDemoSession } from "../lib/demoSession";
import { soft, stagger } from "../lib/motion";
import { BarLoader, Button, Card, CardTitle, Empty, StatusPill } from "../components/ui";
import { errText, useUi } from "../store";

function fmtTime(iso: string) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function eventFailed(ev: RunEvent) {
  if (!ev.actual_result?.ok) return true;
  if (ev.verify && !ev.verify.passed) return true;
  return false;
}

function eventDetail(ev: RunEvent) {
  const sel =
    typeof ev.tool_call?.selector === "string" ? ev.tool_call.selector : "";
  const detail = ev.actual_result?.detail || ev.verify?.actual || "";
  return [sel && `selector=${sel}`, detail].filter(Boolean).join(" · ");
}

export function Logs() {
  const { err, ok, logsSessionId, setLogsSessionId } = useUi();
  const activeDemo = useDemoSession((s) => s.demo);
  const ending = useDemoSession((s) => s.ending);
  const endSession = useDemoSession((s) => s.end);

  const [runs, setRuns] = useState<DemoRun[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [decisions, setDecisions] = useState<DecisionTrace[]>([]);
  const [runTab, setRunTab] = useState<"events" | "decisions">("events");
  const [loadingEvents, setLoadingEvents] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const list = await api.listRuns(DASHBOARD_DAYS);
        if (!alive) return;
        setRuns(list);
      } catch (e) {
        if (alive) err(errText(e));
      }
    };
    load();
    const t = setInterval(load, 4000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [err]);

  useEffect(() => {
    if (!logsSessionId) return;
    setOpen(logsSessionId);
    setLogsSessionId(null);
  }, [logsSessionId, setLogsSessionId]);

  useEffect(() => {
    if (!open) {
      setEvents([]);
      setDecisions([]);
      return;
    }
    let alive = true;
    const load = async () => {
      setLoadingEvents(true);
      try {
        const [rows, dec] = await Promise.all([
          api.runEvents(open),
          api.runDecisions(open).catch(() => [] as DecisionTrace[]),
        ]);
        if (!alive) return;
        setEvents(rows);
        setDecisions(dec);
      } catch (e) {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) {
          setEvents([]);
        } else {
          err(errText(e));
        }
      } finally {
        if (alive) setLoadingEvents(false);
      }
    };
    load();
    const run = runs?.find((r) => r.session_id === open);
    const live =
      (run && (run.status === "starting" || run.status === "running")) ||
      (activeDemo?.session_id === open && demoIsLive(activeDemo));
    const t = live ? setInterval(load, 2000) : undefined;
    return () => {
      alive = false;
      if (t) clearInterval(t);
    };
  }, [open, runs, err, activeDemo]);

  const endRun = async (demoId: string) => {
    try {
      await endSession(demoId);
      ok("Demo ended.");
      const list = await api.listRuns(7);
      setRuns(list);
    } catch (ex) {
      err(errText(ex));
    }
  };

  if (!runs) {
    return (
      <Card interactive={false}>
        <BarLoader label="Loading runs…" />
      </Card>
    );
  }

  return (
    <motion.div
      variants={stagger()}
      initial="hidden"
      animate="show"
      className="grid gap-4"
    >
      <Card span="lg:col-span-2">
        <CardTitle hint={`Last ${DASHBOARD_DAYS} days. Expand for ActionLog. End stops a live session from here too.`}>
          Demo runs
        </CardTitle>
        {!runs.length && <Empty>No runs in the last {DASHBOARD_DAYS} days.</Empty>}
        <ul className="space-y-2">
          {runs.map((r) => {
            const expanded = open === r.session_id;
            const isLive =
              r.status === "starting" || r.status === "running";
            // After reconcile, only runner-backed rows stay starting/running.
            const canEnd =
              isLive ||
              (activeDemo?.demo_id === r.demo_id && demoIsLive(activeDemo));
            return (
              <li
                key={r.session_id}
                className="rounded-lg border"
                style={{ borderColor: "var(--line)" }}
              >
                <div className="flex items-start gap-2 px-3 py-2.5">
                  <button
                    type="button"
                    onClick={() => setOpen(expanded ? null : r.session_id)}
                    className="flex min-w-0 flex-1 items-start gap-3 text-left"
                  >
                    {expanded ? (
                      <ChevronDown size={15} className="mt-0.5 shrink-0 text-[var(--muted)]" />
                    ) : (
                      <ChevronRight size={15} className="mt-0.5 shrink-0 text-[var(--muted)]" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusPill status={isLive ? activeDemo?.status || r.status : r.status} />
                        <span className="text-[0.8rem] font-medium">{r.platform}</span>
                        <span className="text-[0.72rem] text-[var(--muted)]">
                          {fmtTime(r.started_at)}
                        </span>
                        {r.fail_count > 0 && (
                          <span className="text-[0.72rem] text-red-600 dark:text-red-400">
                            {r.fail_count} fail{r.fail_count === 1 ? "" : "s"}
                          </span>
                        )}
                      </div>
                      <p className="mt-1 truncate font-mono text-[0.72rem] text-[var(--muted)]">
                        {[r.host_os, r.host_machine, r.host_name].filter(Boolean).join(" · ")}
                      </p>
                      {r.meeting_label ? (
                        <p className="mt-0.5 truncate font-mono text-[0.72rem]">
                          {r.meeting_label.startsWith("meet:") ? (
                            <a
                              href={`https://meet.google.com/${r.meeting_label.slice(5)}`}
                              target="_blank"
                              rel="noreferrer"
                              className="text-[var(--accent)] hover:underline"
                              onClick={(e) => e.stopPropagation()}
                            >
                              {r.meeting_label}
                            </a>
                          ) : (
                            <span className="text-[var(--muted)]">{r.meeting_label}</span>
                          )}
                        </p>
                      ) : null}
                    </div>
                  </button>
                  {canEnd && (
                    <Button
                      variant="danger"
                      className="shrink-0"
                      disabled={ending}
                      onClick={() => {
                        void endRun(r.demo_id);
                      }}
                    >
                      <PhoneOff size={14} />
                      {ending ? "Ending…" : "End"}
                    </Button>
                  )}
                </div>
                {expanded && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    transition={soft}
                    className="overflow-hidden border-t px-3 py-2"
                    style={{ borderColor: "var(--line)" }}
                  >
                    <div className="mb-2 flex gap-2">
                      <button
                        type="button"
                        className={`rounded px-2 py-1 text-[0.72rem] ${
                          runTab === "events" ? "bg-[var(--accent)]/10 font-medium" : "text-[var(--muted)]"
                        }`}
                        onClick={() => setRunTab("events")}
                      >
                        Action log
                      </button>
                      <button
                        type="button"
                        className={`rounded px-2 py-1 text-[0.72rem] ${
                          runTab === "decisions" ? "bg-[var(--accent)]/10 font-medium" : "text-[var(--muted)]"
                        }`}
                        onClick={() => setRunTab("decisions")}
                      >
                        Agent decisions ({decisions.length})
                      </button>
                    </div>
                    {loadingEvents && !events.length && runTab === "events" ? (
                      <BarLoader label="Loading events…" />
                    ) : runTab === "decisions" ? (
                      !decisions.length ? (
                        <Empty>No agent decisions recorded for this run.</Empty>
                      ) : (
                        <ul className="max-h-80 space-y-2 overflow-y-auto text-[0.72rem]">
                          {decisions.map((d) => (
                            <li
                              key={d.id}
                              className="rounded border px-2 py-1.5"
                              style={{ borderColor: "var(--line)" }}
                            >
                              <div className="flex flex-wrap gap-2 text-[var(--muted)]">
                                <span>{fmtTime(d.created_at)}</span>
                                <span className="font-medium text-[var(--text)]">{d.branch}</span>
                                {d.chosen_flow_id && <span>flow={d.chosen_flow_id}</span>}
                              </div>
                              {d.utterance && (
                                <p className="mt-1">
                                  <span className="text-[var(--muted)]">User:</span> {d.utterance}
                                </p>
                              )}
                              <p className="mt-0.5">
                                <span className="text-[var(--muted)]">Spoke:</span> {d.spoken}
                              </p>
                              {d.detail && (
                                <p className="mt-0.5 text-[var(--muted)]">{d.detail}</p>
                              )}
                            </li>
                          ))}
                        </ul>
                      )
                    ) : !events.length ? (
                      <Empty>No action events yet.</Empty>
                    ) : (
                      <ul className="max-h-80 space-y-1 overflow-y-auto font-mono text-[0.72rem]">
                        {events.map((ev) => (
                          <li
                            key={ev.call_id}
                            className={
                              eventFailed(ev)
                                ? "text-red-700 dark:text-red-400"
                                : "text-[var(--muted)]"
                            }
                          >
                            <span className="text-[var(--text)]">
                              {fmtTime(ev.timestamp)}
                            </span>
                            {" · "}
                            {ev.tool_call?.tool ?? "?"}
                            {" · "}
                            {ev.page}
                            {" · "}
                            {eventFailed(ev) ? "FAIL" : "OK"}
                            {eventDetail(ev) ? ` · ${eventDetail(ev)}` : ""}
                          </li>
                        ))}
                      </ul>
                    )}
                  </motion.div>
                )}
              </li>
            );
          })}
        </ul>
      </Card>
    </motion.div>
  );
}
