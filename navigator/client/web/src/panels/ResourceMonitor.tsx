import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Cpu,
  HardDrive,
  Network,
  Server,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import { LiveMetricChart } from "../components/Chart";
import { BarLoader, Card, CardTitle } from "../components/ui";
import { api, type SystemMetrics } from "../lib/api";
import {
  DISPLAY_TICK_MS,
  formatUptime,
  useNowTick,
  useTickingUptime,
} from "../lib/elapsed";
import { soft } from "../lib/motion";

const HISTORY = 40;

function useNetRates(metrics: SystemMetrics | null) {
  const last = useRef<{ in: number; out: number; time: number } | null>(null);
  const [inMbps, setInMbps] = useState(0);
  const [outMbps, setOutMbps] = useState(0);

  useEffect(() => {
    if (!metrics) return;
    const now = Date.now();
    if (last.current) {
      const dt = (now - last.current.time) / 1000;
      if (dt > 0) {
        setInMbps(
          Math.max(0, ((metrics.net_recv_bytes - last.current.in) / dt / (1024 * 1024)) * 8),
        );
        setOutMbps(
          Math.max(0, ((metrics.net_sent_bytes - last.current.out) / dt / (1024 * 1024)) * 8),
        );
      }
    }
    last.current = { in: metrics.net_recv_bytes, out: metrics.net_sent_bytes, time: now };
  }, [metrics]);

  return { inMbps, outMbps };
}

function statusTone(status: string) {
  if (status === "active") return "text-emerald-600 dark:text-emerald-400 bg-emerald-500/10";
  if (status === "idle") return "text-[var(--muted)] bg-black/[0.04] dark:bg-white/[0.06]";
  return "text-amber-700 dark:text-amber-400 bg-amber-500/10";
}

function UsageBar({ value, color }: { value: number; color: string }) {
  const pct = Math.min(100, Math.max(0, value));
  return (
    <div className="mt-2 h-2 overflow-hidden rounded-full bg-black/[0.06] dark:bg-white/[0.08]">
      <motion.div
        className="h-full rounded-full"
        style={{ background: color }}
        initial={false}
        animate={{ width: `${pct}%` }}
        transition={soft}
      />
    </div>
  );
}

function formatTokens(n: number) {
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function TokenSummary({
  label,
  input,
  output,
  calls,
}: {
  label: string;
  input: number;
  output: number;
  calls: number;
}) {
  return (
    <div className="rounded-lg border px-3 py-2.5" style={{ borderColor: "var(--line)" }}>
      <p className="text-[0.68rem] font-medium uppercase tracking-wide text-[var(--muted)]">{label}</p>
      <p className="mt-1 text-[1.35rem] font-semibold tabular-nums">{formatTokens(input + output)}</p>
      <p className="text-[0.72rem] text-[var(--muted)]">
        {formatTokens(input)} in · {formatTokens(output)} out · {calls} calls
      </p>
    </div>
  );
}

export function ResourceMonitor() {
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cpuHist, setCpuHist] = useState<number[]>([]);
  const [memHist, setMemHist] = useState<number[]>([]);
  const [netInHist, setNetInHist] = useState<number[]>([]);
  const [netOutHist, setNetOutHist] = useState<number[]>([]);
  const { inMbps, outMbps } = useNetRates(metrics);
  const uptimeS = useTickingUptime(metrics?.uptime_s, !!metrics);
  const chartTick = useNowTick(!!metrics);

  useEffect(() => {
    setNetInHist((h) => [...h.slice(-(HISTORY - 1)), inMbps]);
    setNetOutHist((h) => [...h.slice(-(HISTORY - 1)), outMbps]);
  }, [inMbps, outMbps, chartTick]);

  useEffect(() => {
    if (!metrics) return;
    setCpuHist((h) => [...h.slice(-(HISTORY - 1)), metrics.cpu_percent]);
    setMemHist((h) => [...h.slice(-(HISTORY - 1)), metrics.memory_percent]);
  }, [metrics, chartTick]);

  useEffect(() => {
    let alive = true;
    const fetchMetrics = async () => {
      try {
        const data = await api.getSystemMetrics();
        if (!alive) return;
        setMetrics(data);
        setError(null);
      } catch (e) {
        if (!alive) return;
        setError(e instanceof Error ? e.message : "Could not load host metrics");
      }
    };
    void fetchMetrics();
    const t = setInterval(() => void fetchMetrics(), DISPLAY_TICK_MS * 2);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const netMax = Math.max(10, ...netInHist, ...netOutHist, 1);

  if (!metrics && !error) {
    return <BarLoader label="Loading host metrics…" />;
  }

  return (
    <div className="space-y-6 pb-8">
      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-red-500/30 bg-red-500/5 px-4 py-3 text-[0.82rem] text-red-700 dark:text-red-300">
          <AlertCircle size={18} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {metrics && (
        <>
          <div className="flex flex-wrap items-center gap-3 text-[0.75rem] text-[var(--muted)]">
            <span className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1" style={{ borderColor: "var(--line)" }}>
              <Activity size={13} className="text-[var(--accent)]" />
              {metrics.host_label}
            </span>
            <span>Uptime {formatUptime(uptimeS)}</span>
            <span>{metrics.cpu_count} CPU cores</span>
            <span className="ml-auto tabular-nums">Live · 1s tick</span>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Card interactive={false} className="!p-4">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-[0.72rem] font-medium uppercase tracking-wide text-[var(--muted)]">CPU</p>
                  <p className="mt-1 text-[2rem] font-semibold leading-none tabular-nums tracking-tight">
                    {metrics.cpu_percent.toFixed(1)}%
                  </p>
                </div>
                <Cpu size={18} className="text-[var(--accent)]" />
              </div>
              <UsageBar value={metrics.cpu_percent} color="var(--accent)" />
            </Card>

            <Card interactive={false} className="!p-4">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-[0.72rem] font-medium uppercase tracking-wide text-[var(--muted)]">Memory</p>
                  <p className="mt-1 text-[2rem] font-semibold leading-none tabular-nums tracking-tight">
                    {metrics.memory_percent.toFixed(1)}%
                  </p>
                  <p className="mt-1 text-[0.72rem] text-[var(--muted)]">
                    {metrics.memory_used_mb.toFixed(0)} / {metrics.memory_total_mb.toFixed(0)} MB
                  </p>
                </div>
                <HardDrive size={18} className="text-emerald-500" />
              </div>
              <UsageBar value={metrics.memory_percent} color="#10b981" />
            </Card>

            <Card interactive={false} className={`!p-4 ${metrics.gpu.active ? "" : "opacity-80"}`}>
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-[0.72rem] font-medium uppercase tracking-wide text-[var(--muted)]">GPU</p>
                  {metrics.gpu.active ? (
                    <>
                      <p className="mt-1 text-[2rem] font-semibold leading-none tabular-nums">
                        {metrics.gpu.utilization_percent?.toFixed(0)}%
                      </p>
                      <p className="mt-1 line-clamp-2 text-[0.72rem] text-[var(--muted)]">{metrics.gpu.name}</p>
                    </>
                  ) : (
                    <p className="mt-1 text-[1.25rem] font-semibold text-[var(--muted)]">Disabled</p>
                  )}
                </div>
                <Zap size={18} className={metrics.gpu.active ? "text-amber-500" : "text-[var(--muted)]"} />
              </div>
              {metrics.gpu.active && (
                <UsageBar value={metrics.gpu.utilization_percent ?? 0} color="#f59e0b" />
              )}
            </Card>

            <Card interactive={false} className="!p-4">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-[0.72rem] font-medium uppercase tracking-wide text-[var(--muted)]">Network</p>
                  <p className="mt-1 text-[1.35rem] font-semibold tabular-nums">↓ {inMbps.toFixed(2)} Mbps</p>
                  <p className="text-[0.82rem] tabular-nums text-[var(--muted)]">↑ {outMbps.toFixed(2)} Mbps</p>
                </div>
                <Network size={18} className="text-sky-500" />
              </div>
            </Card>
          </div>

          <Card interactive={false}>
            <CardTitle hint="LLM tokens from demos on this product — last 14 days">
              <span className="inline-flex items-center gap-2">
                <Sparkles size={14} /> AI token usage
              </span>
            </CardTitle>
            {!metrics.token_usage ? (
              <p className="text-[0.78rem] text-[var(--muted)]">
                Token metrics unavailable from the server. Hard-refresh this page (Ctrl+Shift+R). If
                it persists, restart Navigator so the API picks up the latest build.
              </p>
            ) : (
              <>
                <p className="mb-4 text-[0.78rem] text-[var(--muted)]">
                  {metrics.token_usage.billing_label}
                </p>

                {metrics.token_usage.has_usage ? (
                  metrics.token_usage.uses_byok ? (
                    <div className="space-y-4">
                      {(metrics.token_usage.platform.calls > 0 ||
                        metrics.token_usage.client.calls > 0) && (
                        <div
                          className={`grid gap-3 ${metrics.token_usage.platform.calls > 0 && metrics.token_usage.client.calls > 0 ? "sm:grid-cols-2" : ""}`}
                        >
                          {metrics.token_usage.platform.calls > 0 && (
                            <TokenSummary
                              label="Platform keys (merged)"
                              input={metrics.token_usage.platform.input_tokens}
                              output={metrics.token_usage.platform.output_tokens}
                              calls={metrics.token_usage.platform.calls}
                            />
                          )}
                          {metrics.token_usage.client.calls > 0 &&
                            metrics.token_usage.client_models.length === 0 && (
                              <TokenSummary
                                label="Your keys (BYOK)"
                                input={metrics.token_usage.client.input_tokens}
                                output={metrics.token_usage.client.output_tokens}
                                calls={metrics.token_usage.client.calls}
                              />
                            )}
                        </div>
                      )}
                      {metrics.token_usage.client_models.length > 0 && (
                        <div className="overflow-x-auto rounded-lg border" style={{ borderColor: "var(--line)" }}>
                          <table className="w-full min-w-[480px] text-left text-[0.78rem]">
                            <thead className="bg-black/[0.03] text-[0.72rem] uppercase tracking-wide text-[var(--muted)] dark:bg-white/[0.04]">
                              <tr>
                                <th className="px-4 py-2.5 font-medium">Model</th>
                                <th className="px-4 py-2.5 font-medium">Input</th>
                                <th className="px-4 py-2.5 font-medium">Output</th>
                                <th className="px-4 py-2.5 font-medium">Calls</th>
                              </tr>
                            </thead>
                            <tbody>
                              {metrics.token_usage.client_models.map((row) => (
                                <tr
                                  key={row.model}
                                  className="border-t"
                                  style={{ borderColor: "var(--line)" }}
                                >
                                  <td className="px-4 py-2.5 font-medium">{row.model}</td>
                                  <td className="px-4 py-2.5 tabular-nums text-[var(--muted)]">
                                    {formatTokens(row.input_tokens)}
                                  </td>
                                  <td className="px-4 py-2.5 tabular-nums text-[var(--muted)]">
                                    {formatTokens(row.output_tokens)}
                                  </td>
                                  <td className="px-4 py-2.5 tabular-nums text-[var(--muted)]">{row.calls}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  ) : (
                    <TokenSummary
                      label="Included usage (merged)"
                      input={metrics.token_usage.platform.input_tokens}
                      output={metrics.token_usage.platform.output_tokens}
                      calls={metrics.token_usage.platform.calls}
                    />
                  )
                ) : (
                  <div className="space-y-3">
                    <p className="text-[0.78rem] text-[var(--muted)]">
                      No recorded usage yet. Run a test demo — totals appear here after LLM calls.
                    </p>
                    {!metrics.token_usage.uses_byok && (
                      <>
                        <p className="text-[0.72rem] font-medium uppercase tracking-wide text-[var(--muted)]">
                          Typical per ~10 min demo
                        </p>
                        <TokenSummary
                          label="Included usage (merged estimate)"
                          input={metrics.token_usage.typical_platform_per_demo.input_tokens}
                          output={metrics.token_usage.typical_platform_per_demo.output_tokens}
                          calls={metrics.token_usage.typical_platform_per_demo.calls}
                        />
                      </>
                    )}
                    {metrics.token_usage.uses_byok && (
                      <p className="text-[0.72rem] text-[var(--muted)]">
                        Your API keys are configured — usage will appear here by model after demos.
                      </p>
                    )}
                  </div>
                )}
              </>
            )}
          </Card>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card interactive={false}>
              <CardTitle hint="Host processor load — last ~60 seconds">CPU usage</CardTitle>
              <LiveMetricChart
                series={[{ values: cpuHist, color: "var(--accent)", label: "CPU", unit: "%" }]}
              />
            </Card>
            <Card interactive={false}>
              <CardTitle hint="RAM consumption on this device">Memory usage</CardTitle>
              <LiveMetricChart
                series={[{ values: memHist, color: "#10b981", label: "Memory", unit: "%" }]}
              />
            </Card>
          </div>

          <Card interactive={false}>
            <CardTitle hint="Download and upload throughput">Network bandwidth</CardTitle>
            <LiveMetricChart
              maxY={netMax}
              yTicks={[0, netMax * 0.25, netMax * 0.5, netMax * 0.75, netMax].map((v) => Math.round(v * 10) / 10)}
              series={[
                { values: netInHist, color: "#0ea5e9", label: "Download", unit: " Mbps" },
                { values: netOutHist, color: "#8b5cf6", label: "Upload", unit: " Mbps" },
              ]}
            />
          </Card>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card interactive={false}>
              <CardTitle hint="What is running for your demos on this host">
                <span className="inline-flex items-center gap-2">
                  <Server size={14} /> Active services
                </span>
              </CardTitle>
              <div className="space-y-2">
                {metrics.services.map((svc) => (
                  <div
                    key={svc.name}
                    className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5"
                    style={{ borderColor: "var(--line)" }}
                  >
                    <div className="min-w-0">
                      <p className="text-[0.82rem] font-medium">{svc.name}</p>
                      <p className="text-[0.72rem] text-[var(--muted)]">{svc.detail}</p>
                    </div>
                    <span
                      className={`shrink-0 rounded-full px-2.5 py-0.5 text-[0.65rem] font-semibold uppercase tracking-wide ${statusTone(svc.status)}`}
                    >
                      {svc.status}
                    </span>
                  </div>
                ))}
              </div>
            </Card>

            <Card interactive={false}>
              <CardTitle hint="Connectivity and storage on this host">
                <span className="inline-flex items-center gap-2">
                  <ShieldCheck size={14} /> Health status
                </span>
              </CardTitle>
              <div className="grid gap-2 sm:grid-cols-2">
                {metrics.health.map((hs) => (
                  <div
                    key={hs.name}
                    className="flex items-start gap-2.5 rounded-lg border px-3 py-2.5"
                    style={{ borderColor: "var(--line)" }}
                  >
                    <CheckCircle2
                      size={16}
                      className={`mt-0.5 shrink-0 ${hs.ok ? "text-emerald-500" : "text-red-500"}`}
                    />
                    <div>
                      <p className="text-[0.82rem] font-medium">{hs.name}</p>
                      {hs.detail && <p className="text-[0.68rem] text-[var(--muted)]">{hs.detail}</p>}
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          </div>

          {metrics.processes.length > 0 && (
            <Card interactive={false}>
              <CardTitle hint="Processes on the machine hosting your demo stack">Host processes</CardTitle>
              <div className="overflow-x-auto rounded-lg border" style={{ borderColor: "var(--line)" }}>
                <table className="w-full min-w-[480px] text-left text-[0.78rem]">
                  <thead className="bg-black/[0.03] text-[0.72rem] uppercase tracking-wide text-[var(--muted)] dark:bg-white/[0.04]">
                    <tr>
                      <th className="px-4 py-2.5 font-medium">Process</th>
                      <th className="px-4 py-2.5 font-medium">CPU</th>
                      <th className="px-4 py-2.5 font-medium">Memory</th>
                      <th className="px-4 py-2.5 font-medium">State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {metrics.processes.map((proc, i) => (
                      <tr key={`${proc.name}-${i}`} className="border-t" style={{ borderColor: "var(--line)" }}>
                        <td className="px-4 py-2.5 font-medium">{proc.name}</td>
                        <td className="px-4 py-2.5 tabular-nums text-[var(--muted)]">{proc.cpu}</td>
                        <td className="px-4 py-2.5 tabular-nums text-[var(--muted)]">{proc.mem}</td>
                        <td className="px-4 py-2.5 text-emerald-600 dark:text-emerald-400">{proc.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
