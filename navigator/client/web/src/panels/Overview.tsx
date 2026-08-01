import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUpRight, CircleCheck, Radio, TriangleAlert, Zap } from "lucide-react";
import { api, type Demo, type Metrics } from "../lib/api";
import { rise, spring, stagger } from "../lib/motion";
import { AreaChart, Sparkbars } from "../components/Chart";
import { BarLoader, Card, CardTitle, Empty } from "../components/ui";
import { errText, useUi } from "../store";

const Kpi = ({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: typeof Zap;
  label: string;
  value: string;
  sub?: string;
}) => (
  <Card>
    <div className="flex items-start justify-between">
      <div>
        <p className="text-[0.72rem] font-medium uppercase tracking-[0.08em] text-[var(--muted)]">
          {label}
        </p>
        <motion.p
          key={value}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={spring}
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

export function Overview() {
  const err = useUi((s) => s.err);
  const [m, setM] = useState<Metrics | null>(null);
  const [demos, setDemos] = useState<Demo[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [metrics, list] = await Promise.all([api.metrics(), api.listDemos()]);
        if (!alive) return;
        setM(metrics);
        setDemos(list);
        setLoadErr(null);
      } catch (e) {
        if (!alive) return;
        const msg = errText(e);
        setLoadErr(msg);
        err(msg);
        setM({
          actions: 0,
          sessions: 0,
          failures: 0,
          verified: 0,
          passed: 0,
          last_seen: null,
          series: [],
          live: { total: 0, running: 0, failed: 0 },
        });
        setDemos([]);
      }
    };
    load();
    const t = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [err]);

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
        label="Sessions"
        value={String(m.sessions)}
        sub={`${m.live.running} running now`}
      />
      <Kpi icon={Zap} label="Actions" value={String(m.actions)} sub="tool calls logged" />
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
        <CardTitle hint="In-process registry — clears when the server restarts.">
          Recent demos
        </CardTitle>
        {demos && demos.length === 0 && <Empty>No demos yet.</Empty>}
        <motion.ul variants={stagger(0.03)} initial="hidden" animate="show" className="space-y-1.5">
          <AnimatePresence initial={false}>
            {(demos ?? [])
              .slice()
              .reverse()
              .map((d) => (
                <motion.li
                  key={d.demo_id}
                  layout
                  variants={rise}
                  exit={{ opacity: 0, x: -8 }}
                  transition={spring}
                  className="flex items-center justify-between rounded-lg border px-3 py-2 text-[0.79rem]"
                  style={{ borderColor: "var(--line)" }}
                >
                  <span className="flex items-center gap-2.5">
                    <span className="font-medium">{d.status}</span>
                    <span className="text-[var(--muted)]">{d.platform ?? "—"}</span>
                    <span className="font-mono text-[0.72rem] text-[var(--muted)]">
                      {d.demo_id.slice(0, 8)}
                    </span>
                  </span>
                  <span className="flex items-center gap-3 text-[var(--muted)]">
                    <span className="font-mono text-[0.72rem]">
                      {d.actions}a / {d.failures}f
                    </span>
                    {d.meeting_url && <ArrowUpRight size={13} />}
                  </span>
                </motion.li>
              ))}
          </AnimatePresence>
        </motion.ul>
      </Card>
    </motion.div>
  );
}
