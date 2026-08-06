import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUpRight, ArrowDownRight, ArrowRight, CircleCheck, Radio, TriangleAlert, Zap } from "lucide-react";
import { api, DASHBOARD_DAYS, type DemoRun, type Metrics, type PublishChecklist } from "../lib/api";
import { formatRunDuration } from "../lib/elapsed";
import { useProductData } from "../lib/productData";
import { soft, stagger } from "../lib/motion";
import { AreaChart, Sparkbars } from "../components/Chart";
import { BarLoader, Card, CardTitle, Empty, StatusPill } from "../components/ui";
import { errText, useUi } from "../store";

const Kpi = ({
  icon: Icon,
  label,
  value,
  sub,
  trend,
  onClick,
}: {
  icon: typeof Zap;
  label: string;
  value: string;
  sub?: string;
  trend?: "up" | "down" | "flat";
  onClick?: () => void;
}) => (
  <Card interactive={!!onClick} onClick={onClick}>
    <div className="flex items-start justify-between">
      <div>
        <div className="flex items-center gap-2">
          <p className="text-[0.72rem] font-medium uppercase tracking-[0.08em] text-[var(--muted)]">
            {label}
          </p>
          {trend === "up" && <ArrowUpRight size={14} className="text-emerald-500" />}
          {trend === "down" && <ArrowDownRight size={14} className="text-red-500" />}
          {trend === "flat" && <ArrowRight size={14} className="text-[var(--muted)]" />}
        </div>
        <motion.p
          key={value}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={soft}
          className="mt-2 text-[1.9rem] font-semibold leading-none tracking-tighter"
        >
          {value}
        </motion.p>
        {sub && <p className="mt-1.5 text-[0.74rem] text-[var(--muted)]">{sub}</p>}
      </div>
      <div className="rounded-lg border p-1.5 text-[var(--muted)]" style={{ borderColor: "var(--line)" }}>
        <Icon size={14} strokeWidth={1.9} />
      </div>
    </div>
  </Card>
);

function calcTrend(series: Metrics["series"], key: "sessions" | "actions" | "failures"): "up" | "down" | "flat" {
  if (series.length < 2) return "flat";
  const mid = Math.floor(series.length / 2);
  const firstHalf = series.slice(0, mid).reduce((sum, d) => sum + d[key], 0);
  const secondHalf = series.slice(mid).reduce((sum, d) => sum + d[key], 0);
  if (secondHalf > firstHalf) return "up";
  if (secondHalf < firstHalf) return "down";
  return "flat";
}

function sumFailCount(runs: DemoRun[]): number {
  return runs.reduce((n, r) => n + r.fail_count, 0);
}

export function Overview() {
  const { err, setTab, setLogsSessionId } = useUi();
  const epoch = useProductData((s) => s.epoch);
  const [m, setM] = useState<Metrics | null>(null);
  const [runs, setRuns] = useState<DemoRun[] | null>(null);
  const [checklist, setChecklist] = useState<PublishChecklist | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [metrics, runList, pub] = await Promise.all([
          api.metrics(DASHBOARD_DAYS),
          api.listRuns(DASHBOARD_DAYS),
          api.getPublishChecklist().catch(() => null),
        ]);
        if (!alive) return;
        setM(metrics);
        setRuns(runList);
        setChecklist(pub);
        setLoadErr(null);
      } catch (e) {
        if (!alive) return;
        const msg = errText(e);
        setLoadErr(msg);
        err(msg);
        setM({
          test_sessions: 0,
          actions: 0,
          sessions: 0,
          failures: 0,
          failed_runs: 0,
          verified: 0,
          passed: 0,
          last_seen: null,
          series: [],
          run_series: [],
          demos: { total: 0, running: 0, failed: 0 },
          live: { total: 0, running: 0, failed: 0 },
          test: { total: 0, running: 0, failed: 0 },
        });
        setRuns([]);
      }
    };
    load();
    const t = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [err, epoch]);

  if (!m) {
    return (
      <Card interactive={false}>
        <BarLoader label="Loading metrics…" />
      </Card>
    );
  }

  const passRate = m.verified > 0 ? Math.round((m.passed / m.verified) * 100) : null;
  const failedRuns = m.failed_runs ?? m.demos?.failed ?? 0;
  const runsWithStepFails = m.runs_with_step_failures ?? null;
  const windowDays = m.days ?? DASHBOARD_DAYS;
  const tableFailSum = runs ? sumFailCount(runs) : null;

  return (
    <motion.div
      variants={stagger()}
      initial="hidden"
      animate="show"
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
    >
      {loadErr && (
        <Card span="sm:col-span-2 xl:col-span-4" interactive={false}>
          <p className="text-[0.8rem] text-amber-700 dark:text-amber-400">
            Could not refresh metrics ({loadErr}). Showing zeros — try Log out / Log in.
          </p>
        </Card>
      )}
      <Kpi
        icon={Radio}
        label="Demo sessions"
        value={String(m.sessions)}
        sub={`${m.live.total} live · ${m.test?.total ?? m.test_sessions} test · last ${windowDays}d`}
        trend={calcTrend(m.series, "sessions")}
        onClick={() => setTab("logs")}
      />
      <Kpi
        icon={Zap}
        label="Actions"
        value={String(m.actions)}
        sub={`tool steps · last ${windowDays}d`}
        trend={calcTrend(m.series, "actions")}
        onClick={() => setTab("logs")}
      />
      <Kpi
        icon={CircleCheck}
        label="Pass rate"
        value={passRate === null ? "—" : `${passRate}%`}
        sub={passRate === null ? "no verified steps" : `${m.passed}/${m.verified} verified steps`}
      />
      <Kpi
        icon={TriangleAlert}
        label="Step failures"
        value={String(m.failures)}
        sub={`${runsWithStepFails ?? "—"} demos affected · last ${windowDays}d`}
        trend={calcTrend(m.series, "failures")}
        onClick={() => setTab("logs")}
      />

      {checklist && (
        <Card span="sm:col-span-2 xl:col-span-4">
          <CardTitle hint="Pre-publish gate for live visitors — flows, knowledge, eval.">
            Publish checklist
          </CardTitle>
          <div className="mb-2 flex flex-wrap items-center gap-3 text-[0.78rem]">
            <span>Readiness {checklist.readiness.score}/100</span>
            {checklist.eval_score_pct != null && (
              <span>Eval {Math.round(checklist.eval_score_pct)}%</span>
            )}
            <span className="text-[var(--muted)]">{checklist.autonomy_recommendation}</span>
          </div>
          <ul className="grid gap-1 sm:grid-cols-2 text-[0.72rem]">
            {checklist.readiness.checks.map((c) => (
              <li
                key={c.id}
                className={
                  c.ok
                    ? "text-emerald-700 dark:text-emerald-400"
                    : c.blocking
                      ? "text-red-700 dark:text-red-400"
                      : "text-amber-700 dark:text-amber-400"
                }
              >
                {c.ok ? "✓" : c.blocking ? "✕" : "!"} {c.message}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card span="sm:col-span-2 xl:col-span-3">
        <CardTitle hint={`Last ${windowDays} days — actions, demo sessions, and step failures share one window and data source.`}>
          Activity
        </CardTitle>
        <AreaChart series={m.series} />
      </Card>

      <Card span="sm:col-span-2 xl:col-span-1">
        <CardTitle hint={`Demo runs per day — totals match the KPIs above (last ${windowDays} days).`}>
          Sessions
        </CardTitle>
        <Sparkbars series={m.series} />
        <div className="mt-4 space-y-2 border-t pt-3" style={{ borderColor: "var(--line)" }}>
          {[
            ["Total demos", m.demos?.total ?? m.sessions],
            ["Running", m.demos?.running ?? 0],
            ["Step failures", m.failures],
            ["Demos w/ step fails", runsWithStepFails ?? "—"],
            ["Crashed demos", failedRuns],
          ].map(([k, v]) => (
            <div key={String(k)} className="flex justify-between text-[0.78rem]">
              <span className="text-[var(--muted)]">{k}</span>
              <span className="font-mono">{v}</span>
            </div>
          ))}
          {m.visitor && (
            <p className="pt-1 text-[0.68rem] text-[var(--muted)]">
              Billable visitors only: {m.visitor.sessions} sessions · {m.visitor.actions} actions
            </p>
          )}
          <p className="text-[0.65rem] leading-snug text-[var(--muted)]">
            Step failures = bad tool/verify steps. Crashed demos = run ended with error (e.g. bot
            never joined).
          </p>
        </div>
      </Card>

      <Card span="sm:col-span-2 xl:col-span-4">
        <CardTitle hint={`Last ${windowDays} days — step failures column sums to ${m.failures} (table total ${tableFailSum ?? "…"}). Click a row for Logs.`}>
          Recent runs
        </CardTitle>
        {runs && runs.length === 0 && <Empty>No demos run recently.</Empty>}
        <div className="-mx-2 overflow-x-auto">
          <table className="w-full min-w-[600px] text-left text-[0.8rem]">
            <thead>
              <tr className="border-b text-[0.72rem] uppercase tracking-wider text-[var(--muted)]" style={{ borderColor: "var(--line)" }}>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Platform</th>
                <th className="px-3 py-2 font-medium">Origin</th>
                <th className="px-3 py-2 font-medium">Started</th>
                <th className="px-3 py-2 font-medium">Duration</th>
                <th className="px-3 py-2 font-medium">Step fails</th>
              </tr>
            </thead>
            <tbody>
              <AnimatePresence initial={false}>
                {(runs ?? []).slice(0, 6).map((run) => {
                  const duration =
                    run.started_at && run.ended_at
                      ? Math.round(
                          (new Date(run.ended_at).getTime() - new Date(run.started_at).getTime()) / 1000
                        )
                      : null;
                  return (
                    <motion.tr
                      key={run.session_id}
                      layout
                      initial={{ opacity: 0, y: -5 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      className="group cursor-pointer border-b hover:bg-black/[0.02] dark:hover:bg-white/[0.02]"
                      style={{ borderColor: "var(--line)" }}
                      onClick={() => {
                        setLogsSessionId(run.session_id);
                        setTab("logs");
                      }}
                    >
                      <td className="px-3 py-2.5">
                        <StatusPill status={run.status} />
                      </td>
                      <td className="px-3 py-2.5 font-medium">{run.platform}</td>
                      <td className="px-3 py-2.5">
                        <span className="rounded bg-black/[0.04] px-1.5 py-0.5 text-[0.72rem] dark:bg-white/[0.06]">
                          {run.origin}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-[var(--muted)]">
                        {run.started_at ? new Date(run.started_at).toLocaleString() : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-[var(--muted)]">
                        {duration !== null ? formatRunDuration(duration) : "—"}
                      </td>
                      <td className="px-3 py-2.5">
                        {run.fail_count > 0 ? (
                          <span className="text-red-500">{run.fail_count}</span>
                        ) : (
                          <span className="text-[var(--muted)]">0</span>
                        )}
                      </td>
                    </motion.tr>
                  );
                })}
              </AnimatePresence>
            </tbody>
          </table>
        </div>
      </Card>
    </motion.div>
  );
}
