import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api, ApiError, type DemoRun, type RunEvent } from "../lib/api";
import { spring, stagger } from "../lib/motion";
import { BarLoader, Card, CardTitle, Empty, StatusPill } from "../components/ui";
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
  const { err, logsSessionId, setLogsSessionId } = useUi();
  const [runs, setRuns] = useState<DemoRun[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [loadingEvents, setLoadingEvents] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const list = await api.listRuns(7);
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
      return;
    }
    let alive = true;
    const load = async () => {
      setLoadingEvents(true);
      try {
        const rows = await api.runEvents(open);
        if (!alive) return;
        setEvents(rows);
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
    const live = run && (run.status === "starting" || run.status === "running");
    const t = live ? setInterval(load, 2000) : undefined;
    return () => {
      alive = false;
      if (t) clearInterval(t);
    };
  }, [open, runs, err]);

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
        <CardTitle hint="Last 7 days. Expand a run for full ActionLog (selectors, errors). Never spoken to prospects.">
          Demo runs
        </CardTitle>
        {!runs.length && <Empty>No runs in the last 7 days.</Empty>}
        <ul className="space-y-2">
          {runs.map((r) => {
            const expanded = open === r.session_id;
            return (
              <li
                key={r.session_id}
                className="rounded-lg border"
                style={{ borderColor: "var(--line)" }}
              >
                <button
                  type="button"
                  onClick={() => setOpen(expanded ? null : r.session_id)}
                  className="flex w-full items-start gap-3 px-3 py-2.5 text-left"
                >
                  {expanded ? (
                    <ChevronDown size={15} className="mt-0.5 shrink-0 text-[var(--muted)]" />
                  ) : (
                    <ChevronRight size={15} className="mt-0.5 shrink-0 text-[var(--muted)]" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusPill status={r.status} />
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
                      {r.meeting_label ? ` · ${r.meeting_label}` : ""}
                    </p>
                  </div>
                </button>
                {expanded && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    transition={spring}
                    className="border-t px-3 py-2"
                    style={{ borderColor: "var(--line)" }}
                  >
                    {loadingEvents && !events.length ? (
                      <BarLoader label="Loading events…" />
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
