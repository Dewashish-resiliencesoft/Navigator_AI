import { motion } from "motion/react";
import type { MetricPoint } from "../lib/api";

const W = 520;
const H = 130;
const PAD = 4;

/** Area chart whose stroke draws itself on mount. Hand-rolled: one series,
 *  no axes — a chart lib would be more bytes than the 30 lines it replaces. */
export function AreaChart({ series }: { series: MetricPoint[] }) {
  if (series.length === 0) {
    return (
      <div className="flex h-[130px] items-center justify-center text-[0.8rem] text-[var(--muted)]">
        No activity logged yet.
      </div>
    );
  }

  const pts = series.length === 1 ? [series[0], series[0]] : series;
  const max = Math.max(...pts.map((p) => p.actions), 1);
  const step = (W - PAD * 2) / (pts.length - 1);

  const coords = pts.map((p, i) => ({
    x: PAD + i * step,
    y: H - PAD - (p.actions / max) * (H - PAD * 2),
  }));

  const line = coords
    .map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)},${c.y.toFixed(1)}`)
    .join(" ");
  const area = `${line} L${(W - PAD).toFixed(1)},${H - PAD} L${PAD},${H - PAD} Z`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Actions per day">
      <defs>
        <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.28" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
        </linearGradient>
      </defs>

      <motion.path
        d={area}
        fill="url(#areaFill)"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5, delay: 0.55 }}
      />
      <motion.path
        d={line}
        fill="none"
        stroke="var(--accent)"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 1.1, ease: "easeInOut" }}
      />
      {coords.map((c, i) => (
        <motion.circle
          key={i}
          cx={c.x}
          cy={c.y}
          r="2.5"
          fill="var(--accent)"
          initial={{ opacity: 0, scale: 0 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.6 + i * 0.03, type: "spring", stiffness: 300 }}
        />
      ))}
    </svg>
  );
}

export function Sparkbars({ series }: { series: MetricPoint[] }) {
  const max = Math.max(...series.map((s) => s.sessions), 1);
  return (
    <div className="flex h-10 items-end gap-1">
      {series.map((s, i) => (
        <motion.div
          key={s.day}
          className="flex-1 rounded-sm bg-[var(--accent)]/35"
          initial={{ height: 0 }}
          animate={{ height: `${Math.max((s.sessions / max) * 100, 4)}%` }}
          transition={{ delay: i * 0.03, type: "spring", stiffness: 140, damping: 16 }}
          title={`${s.day}: ${s.sessions} sessions`}
        />
      ))}
    </div>
  );
}
