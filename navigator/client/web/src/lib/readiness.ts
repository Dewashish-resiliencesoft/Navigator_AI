import type { ReadinessCheck } from "./api";

/** Platform infra — never show in Client dashboard readiness lists. */
const CLIENT_HIDDEN_READINESS_IDS = new Set([
  "knowledge_fresh",
  "groq",
  "gemini",
  "tts",
  "attendee",
]);

export function clientVisibleReadinessChecks(
  checks: ReadinessCheck[],
): ReadinessCheck[] {
  return checks.filter((c) => !CLIENT_HIDDEN_READINESS_IDS.has(c.id));
}

export function clientReadinessScore(checks: ReadinessCheck[]): number {
  const visible = clientVisibleReadinessChecks(checks);
  if (!visible.length) return 0;
  return Math.round(
    (100 * visible.filter((c) => c.ok).length) / visible.length,
  );
}
