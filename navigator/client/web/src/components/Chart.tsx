import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import type { MetricPoint } from "../lib/api";
import { cn } from "../lib/cn";
import { Tooltip } from "./ui";

const W = 520;
const H = 160;
const PAD_X = 20;
const PAD_Y = 16;
const BOTTOM_LABEL_SPACE = 24;

export function AreaChart({ series }: { series: MetricPoint[] }) {
  const [metric, setMetric] = useState<"actions" | "sessions" | "failures">("actions");
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);

  if (series.length === 0) {
    return (
      <div className="flex h-[160px] items-center justify-center text-[0.8rem] text-[var(--muted)]">
        No activity logged yet.
      </div>
    );
  }

  const pts = series.length === 1 ? [series[0], series[0]] : series;
  const maxVal = Math.max(...pts.map((p) => p[metric]), 1);
  const chartW = W - PAD_X * 2;
  const chartH = H - PAD_Y * 2 - BOTTOM_LABEL_SPACE;
  const step = chartW / (pts.length - 1);

  const coords = pts.map((p, i) => ({
    x: PAD_X + i * step,
    y: PAD_Y + chartH - (p[metric] / maxVal) * chartH,
  }));

  const line = coords
    .map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)},${c.y.toFixed(1)}`)
    .join(" ");
  const area = `${line} L${(PAD_X + chartW).toFixed(1)},${PAD_Y + chartH} L${PAD_X},${PAD_Y + chartH} Z`;

  const color =
    metric === "actions"
      ? "var(--accent)"
      : metric === "sessions"
        ? "#10b981"
        : "#f43f5e";

  return (
    <div className="relative">
      <div className="mb-4 flex gap-2">
        {(["actions", "sessions", "failures"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMetric(m)}
            className={cn(
              "rounded px-2 py-1 text-[0.72rem] font-medium uppercase tracking-wider transition-colors",
              metric === m
                ? "bg-[var(--text)] text-[var(--bg)]"
                : "text-[var(--muted)] hover:bg-black/[0.04] dark:hover:bg-white/[0.06]",
            )}
          >
            {m}
          </button>
        ))}
      </div>

      <div
        className="relative"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const mx = e.clientX - rect.left;
          // Scale mx to SVG width
          const svgX = (mx / rect.width) * W;
          if (svgX < PAD_X || svgX > W - PAD_X) {
            setHover(null);
            return;
          }
          const i = Math.min(Math.max(Math.round((svgX - PAD_X) / step), 0), pts.length - 1);
          setHover({ i, x: coords[i].x, y: coords[i].y });
        }}
      >
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Activity chart">
          <defs>
            <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.28" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>

          {/* Grid lines */}
          {[0, 0.5, 1].map((ratio) => (
            <line
              key={ratio}
              x1={PAD_X}
              y1={PAD_Y + chartH * ratio}
              x2={W - PAD_X}
              y2={PAD_Y + chartH * ratio}
              stroke="var(--line)"
              strokeDasharray="4 4"
            />
          ))}

          {/* Axis Labels */}
          <text x={0} y={PAD_Y} fill="var(--muted)" fontSize="10" dominantBaseline="middle">
            {maxVal}
          </text>
          <text x={0} y={PAD_Y + chartH} fill="var(--muted)" fontSize="10" dominantBaseline="middle">
            0
          </text>

          {/* X Axis Labels */}
          <text x={PAD_X} y={H - 4} fill="var(--muted)" fontSize="10">
            {pts[0].day.slice(5)}
          </text>
          {pts.length > 2 && (
            <text x={W / 2} y={H - 4} fill="var(--muted)" fontSize="10" textAnchor="middle">
              {pts[Math.floor(pts.length / 2)].day.slice(5)}
            </text>
          )}
          <text x={W - PAD_X} y={H - 4} fill="var(--muted)" fontSize="10" textAnchor="end">
            {pts[pts.length - 1].day.slice(5)}
          </text>

          {/* Crosshair */}
          <AnimatePresence>
            {hover && (
              <motion.line
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                x1={hover.x}
                y1={PAD_Y}
                x2={hover.x}
                y2={PAD_Y + chartH}
                stroke="var(--muted)"
                strokeDasharray="2 2"
              />
            )}
          </AnimatePresence>

          <motion.path
            key={`area-${metric}`}
            d={area}
            fill="url(#areaFill)"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.5 }}
          />
          <motion.path
            key={`line-${metric}`}
            d={line}
            fill="none"
            stroke={color}
            strokeWidth="1.75"
            strokeLinecap="round"
            strokeLinejoin="round"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 0.8, ease: "easeInOut" }}
          />
          {coords.map((c, i) => (
            <motion.circle
              key={`pt-${metric}-${i}`}
              cx={c.x}
              cy={c.y}
              r={hover?.i === i ? "4" : "2.5"}
              fill={color}
              initial={{ opacity: 0, scale: 0 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: i * 0.02, type: "spring", stiffness: 300 }}
            />
          ))}
        </svg>

        {/* Hover Tooltip HTML */}
        <AnimatePresence>
          {hover && (
            <motion.div
              initial={{ opacity: 0, y: 5 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 5 }}
              transition={{ duration: 0.1 }}
              className="chart-tooltip absolute -top-1 z-10 w-max -translate-x-1/2 -translate-y-full rounded-md border bg-[var(--panel)] px-2.5 py-1.5 text-center shadow-lg"
              style={{
                left: `${(hover.x / W) * 100}%`,
                borderColor: "var(--line)",
              }}
            >
              <p className="text-[0.65rem] text-[var(--muted)]">
                {pts[hover.i].day}
              </p>
              <p className="text-[0.8rem] font-semibold text-[var(--text)]">
                {pts[hover.i][metric]} {metric}
              </p>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

export function Sparkbars({ series }: { series: MetricPoint[] }) {
  const max = Math.max(...series.map((s) => s.sessions), 1);
  return (
    <div className="flex h-10 items-end gap-1">
      {series.map((s, i) => (
        <Tooltip key={s.day} content={`${s.day}: ${s.sessions} sessions`}>
          <motion.div
            className="flex-1 rounded-sm bg-[var(--accent)]/35 w-2"
            initial={{ height: 0 }}
            animate={{ height: `${Math.max((s.sessions / max) * 100, 4)}%` }}
            transition={{ delay: i * 0.03, type: "spring", stiffness: 140, damping: 16 }}
          />
        </Tooltip>
      ))}
    </div>
  );
}

const LIVE_W = 640;
const LIVE_H = 200;
const LIVE_PAD = { l: 44, r: 16, t: 16, b: 32 };

function liveCoords(values: number[], maxY: number) {
  const pts = values.length === 1 ? [values[0], values[0]] : values;
  const chartW = LIVE_W - LIVE_PAD.l - LIVE_PAD.r;
  const chartH = LIVE_H - LIVE_PAD.t - LIVE_PAD.b;
  const step = pts.length > 1 ? chartW / (pts.length - 1) : 0;
  return pts.map((v, i) => ({
    x: LIVE_PAD.l + i * step,
    y: LIVE_PAD.t + chartH - (Math.min(maxY, Math.max(0, v)) / maxY) * chartH,
    v,
  }));
}

function livePath(coords: { x: number; y: number }[]) {
  return coords.map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");
}

function liveArea(coords: { x: number; y: number }[]) {
  const chartH = LIVE_H - LIVE_PAD.t - LIVE_PAD.b;
  const base = LIVE_PAD.t + chartH;
  const line = livePath(coords);
  const last = coords[coords.length - 1];
  const first = coords[0];
  return `${line} L${last.x.toFixed(1)},${base} L${first.x.toFixed(1)},${base} Z`;
}

type LiveSeries = { values: number[]; color: string; label: string; unit?: string };

export function LiveMetricChart({
  series,
  maxY = 100,
  yTicks = [0, 25, 50, 75, 100],
}: {
  series: LiveSeries[];
  maxY?: number;
  yTicks?: number[];
}) {
  const [hover, setHover] = useState<{ i: number; x: number } | null>(null);
  const primary = series[0]?.values ?? [];
  if (primary.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border text-[0.8rem] text-[var(--muted)]"
        style={{ borderColor: "var(--line)", height: LIVE_H }}
      >
        Collecting samples…
      </div>
    );
  }

  const allCoords = series.map((s) => liveCoords(s.values, maxY));
  const chartH = LIVE_H - LIVE_PAD.t - LIVE_PAD.b;
  const step =
    primary.length > 1 ? (LIVE_W - LIVE_PAD.l - LIVE_PAD.r) / (primary.length - 1) : 0;

  return (
    <div className="relative">
      {series.length > 1 && (
        <div className="mb-3 flex flex-wrap gap-3">
          {series.map((s) => (
            <span key={s.label} className="flex items-center gap-1.5 text-[0.72rem] text-[var(--muted)]">
              <span className="h-2 w-2 rounded-full" style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </div>
      )}
      <div
        className="relative rounded-xl border bg-black/[0.02] dark:bg-white/[0.02]"
        style={{ borderColor: "var(--line)" }}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const svgX = ((e.clientX - rect.left) / rect.width) * LIVE_W;
          if (svgX < LIVE_PAD.l || svgX > LIVE_W - LIVE_PAD.r) {
            setHover(null);
            return;
          }
          const i = Math.min(
            Math.max(Math.round((svgX - LIVE_PAD.l) / (step || 1)), 0),
            primary.length - 1,
          );
          setHover({ i, x: allCoords[0][i].x });
        }}
      >
        <svg viewBox={`0 0 ${LIVE_W} ${LIVE_H}`} className="w-full" role="img">
          <defs>
            {series.map((s, idx) => (
              <linearGradient key={s.label} id={`liveFill-${idx}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity="0.22" />
                <stop offset="100%" stopColor={s.color} stopOpacity="0" />
              </linearGradient>
            ))}
          </defs>

          {yTicks.map((tick) => {
            const y = LIVE_PAD.t + chartH - (tick / maxY) * chartH;
            return (
              <g key={tick}>
                <line
                  x1={LIVE_PAD.l}
                  y1={y}
                  x2={LIVE_W - LIVE_PAD.r}
                  y2={y}
                  stroke="var(--line)"
                  strokeDasharray={tick === 0 ? undefined : "4 4"}
                />
                <text x={LIVE_PAD.l - 8} y={y} fill="var(--muted)" fontSize="10" textAnchor="end" dominantBaseline="middle">
                  {tick}
                </text>
              </g>
            );
          })}

          {hover && (
            <line
              x1={hover.x}
              y1={LIVE_PAD.t}
              x2={hover.x}
              y2={LIVE_PAD.t + chartH}
              stroke="var(--muted)"
              strokeDasharray="3 3"
              opacity={0.7}
            />
          )}

          {allCoords.map((coords, idx) => (
            <g key={series[idx].label}>
              {idx === 0 && (
                <path d={liveArea(coords)} fill={`url(#liveFill-${idx})`} />
              )}
              <path
                d={livePath(coords)}
                fill="none"
                stroke={series[idx].color}
                strokeWidth="2.25"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              {coords.map((c, i) => (
                <circle
                  key={i}
                  cx={c.x}
                  cy={c.y}
                  r={hover?.i === i ? 4.5 : 0}
                  fill={series[idx].color}
                  className="transition-all duration-150"
                />
              ))}
            </g>
          ))}

          <text x={LIVE_PAD.l} y={LIVE_H - 8} fill="var(--muted)" fontSize="10">
            −{Math.round((primary.length * 1.5) / 60)}m
          </text>
          <text x={LIVE_W - LIVE_PAD.r} y={LIVE_H - 8} fill="var(--muted)" fontSize="10" textAnchor="end">
            now
          </text>
        </svg>

        {hover && (
          <div
            className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-lg border bg-[var(--panel)] px-3 py-2 shadow-lg"
            style={{
              left: `${(hover.x / LIVE_W) * 100}%`,
              top: "12%",
              borderColor: "var(--line)",
            }}
          >
            {series.map((s) => (
              <p key={s.label} className="text-[0.72rem]">
                <span className="text-[var(--muted)]">{s.label}: </span>
                <span className="font-semibold tabular-nums">
                  {s.values[hover.i]?.toFixed(1)}
                  {s.unit ?? "%"}
                </span>
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

