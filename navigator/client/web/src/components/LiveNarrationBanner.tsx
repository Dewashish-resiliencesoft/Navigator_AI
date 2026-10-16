import { demoIsLive, useDemoSession } from "../lib/demoSession";

const LANG: Record<string, { label: string; flag: string }> = {
  en: { label: "English", flag: "🇬🇧" },
  hi: { label: "Hindi", flag: "🇮🇳" },
  es: { label: "Spanish", flag: "🇪🇸" },
};

function statusLabel(status?: string) {
  if (status === "speaking") return "Speaking";
  if (status === "listening") return "Listening";
  if (status === "thinking" || status === "tailoring") return "Preparing";
  return status ? status[0].toUpperCase() + status.slice(1) : "Idle";
}

/** Live Demo Script: detected language + the line Navigator is about to say. */
export function LiveNarrationBanner() {
  const demo = useDemoSession((s) => s.demo);
  if (!demoIsLive(demo) || !demo) return null;
  const user = (demo.language || "en").toLowerCase();
  const tts = (demo.language_code || user || "en").toLowerCase();
  const meta = LANG[user] || { label: user, flag: "" };
  const ttsMeta = LANG[tts] || { label: tts, flag: "" };
  const line = (demo.current_narration || "").trim();
  const switched = user !== tts;

  return (
    <div
      className="mb-4 rounded-lg border px-3 py-2.5 text-[0.78rem]"
      style={{ borderColor: "var(--line)" }}
    >
      <div className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.68rem] font-medium uppercase tracking-wide text-[var(--muted)]">
        <span>
          Language: {meta.label} {meta.flag}
        </span>
        {switched && (
          <span className="normal-case font-normal text-amber-700 dark:text-amber-400">
            TTS fallback {ttsMeta.label} {ttsMeta.flag}
          </span>
        )}
        <span>Status: {statusLabel(demo.speech_status)}</span>
      </div>
      {line ? (
        <p className="text-[0.88rem] leading-relaxed text-[var(--text)]">“{line}”</p>
      ) : (
        <p className="text-[var(--muted)]">Waiting for the next line…</p>
      )}
    </div>
  );
}
