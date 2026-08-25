/**
 * Wall-clock elapsed seconds — display ticks every 1s, no 1→3 jumps from poll sync.
 */

import { useEffect, useState } from "react";

/** UI counters (clocks, elapsed timers) — always 1 second. */
export const DISPLAY_TICK_MS = 1000;

export function elapsedSecondsSince(
  anchorMs: number | null | undefined,
  now = Date.now(),
): number {
  if (anchorMs == null) return 0;
  return Math.max(0, Math.floor((now - anchorMs) / 1000));
}

export function anchorFromElapsedSeconds(elapsedS: number, now = Date.now()): number {
  return now - Math.max(0, Math.floor(elapsedS)) * 1000;
}

/** Re-anchor only when local clock drifted far from server — avoids poll skips. */
export function syncElapsedAnchor(
  currentAnchor: number | null,
  serverElapsedS: number | undefined,
  thresholdS = 2,
  now = Date.now(),
): number | null {
  if (serverElapsedS == null || !Number.isFinite(serverElapsedS)) {
    return currentAnchor;
  }
  const target = anchorFromElapsedSeconds(serverElapsedS, now);
  if (currentAnchor == null) return target;
  const localS = elapsedSecondsSince(currentAnchor, now);
  const serverS = Math.floor(serverElapsedS);
  if (Math.abs(localS - serverS) > thresholdS) return target;
  return currentAnchor;
}

/** Hook: `Date.now()` bumped every second while `ticking`. */
export function useNowTick(ticking: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), DISPLAY_TICK_MS);
    return () => clearInterval(t);
  }, [ticking]);
  return now;
}

/** Elapsed whole seconds from anchor; ticks each 1s when `ticking`. */
export function useElapsedSeconds(
  anchorMs: number | null,
  ticking: boolean,
): number {
  const now = useNowTick(ticking);
  if (anchorMs == null) return 0;
  return elapsedSecondsSince(anchorMs, ticking ? now : Date.now());
}

/** mm:ss or h:mm:ss clock. */
export function formatElapsedClock(totalSeconds: number): string {
  const sec = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const r = sec % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
  return `${m}:${String(r).padStart(2, "0")}`;
}

/** Run duration for logs/history — ≤120s as `35s`; longer as `7m 2s` / `1h 7m 2s`. */
export function formatRunDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s <= 120) return `${s}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}h ${m}m ${r}s`;
  return `${m}m ${r}s`;
}

/** Server uptime snapshot + local 1s tick between polls. */
export function useTickingUptime(
  serverUptimeS: number | undefined,
  active: boolean,
): number {
  const [base, setBase] = useState({ s: 0, at: Date.now() });
  useEffect(() => {
    if (serverUptimeS == null || !Number.isFinite(serverUptimeS)) return;
    setBase({ s: serverUptimeS, at: Date.now() });
  }, [serverUptimeS]);
  const now = useNowTick(active);
  return Math.max(0, Math.floor(base.s + (now - base.at) / 1000));
}

export function formatUptime(totalSeconds: number): string {
  return formatElapsedClock(totalSeconds);
}

/** @deprecated use formatElapsedClock */
export const formatExploreElapsed = formatElapsedClock;
