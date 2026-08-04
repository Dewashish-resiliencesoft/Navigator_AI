import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUpRight, ArrowDownRight, ArrowRight, CircleCheck, Radio, TriangleAlert, Zap } from "lucide-react";
import { api, type DemoRun, type Metrics } from "../lib/api";
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

export function Overview() {
  const { err, setTab, setLogsSessionId } = useUi();
  const epoch = useProductData((s) => s.epoch);
  const [m, setM] = useState<Metrics | null>(null);
  const [runs, setRuns] = useState<DemoRun[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [metrics, runList] = await Promise.all([api.metrics(), api.listRuns(7)]);
        if (!alive) return;
        setM(metrics);
        setRuns(runList);
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
          verified: 0,
          passed: 0,
          last_seen: null,
          series: [],
          live: { total: 0, running: 0, failed: 0 },
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
        label="Visitor sessions"
        value={String(m.sessions)}
        sub={`${m.live.running} running now · ${m.test_sessions} test`}
        trend={calcTrend(m.series, "sessions")}
        onClick={() => setTab("logs")}
      />
      <Kpi
        icon={Zap}
        label="Actions"
        value={String(m.actions)}
        sub="tool calls logged"
        trend={calcTrend(m.series, "actions")}
        onClick={() => setTab("logs")}
      />
      <Kpi
        icon={CircleCheck}
        label="Pass rate"
        value={passRate === null ? "—" : `${passRate}%`}
        sub={passRate === null ? "no verified steps" : `${m.passed}/${m.verified} verified`}
      />
      <Kpi
        icon={TriangleAlert}
        label="Failures"
        value={String(m.failures)}
        sub={m.last_seen ? `last ${m.last_seen.slice(0, 10)}` : "never run"}
        trend={calcTrend(m.series, "failures")}
        onClick={() => setTab("logs")}
      />

      <Card span="sm:col-span-2 xl:col-span-3">
        <CardTitle hint="Tool calls per day, from the durable action log.">
          Activity
        </CardTitle>
        <AreaChart series={m.series} />
      </Card>

      <Card span="sm:col-span-2 xl:col-span-1">
        <CardTitle hint="Sessions per day.">Sessions</CardTitle>
        <Sparkbars series={m.series} />
        <div className="mt-4 space-y-2 border-t pt-3" style={{ borderColor: "var(--line)" }}>
          {[
            ["Live total", m.live.total],
            ["Running", m.live.running],
            ["Failed", m.live.failed],
          ].map(([k, v]) => (
            <div key={String(k)} className="flex justify-between text-[0.78rem]">
              <span className="text-[var(--muted)]">{k}</span>
              <span className="font-mono">{v}</span>
            </div>
          ))}
        </div>
      </Card>

      <Card span="sm:col-span-2 xl:col-span-4">
        <CardTitle hint="Last 7 days of demo runs — click to expand in Logs.">
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
                <th className="px-3 py-2 font-medium">Failures</th>
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
                        {duration !== null ? `${duration}s` : "—"}
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
