import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, MessageSquare, Monitor, RefreshCw, Save, ShieldAlert, UserCircle } from "lucide-react";
import { api, INPUT_TYPES, type DemoScriptBeat, type DemoScriptResponse } from "../lib/api";
import { LiveNarrationBanner } from "./LiveNarrationBanner";
import { BarLoader, Button, Card, CardTitle, Empty, Textarea } from "./ui";
import { errText, useUi } from "../store";

function sourceBadge(source?: string) {
  if (!source || source === "generated") return null;
  const labels: Record<string, string> = {
    manual: "Manual",
    yaml: "YAML",
    explore: "Explore",
    semantics: "Semantics",
    recorded: "Recorded",
    intake: "Intake",
    knowledge: "Knowledge",
  };
  const label = labels[source] || source;
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[0.62rem] font-medium uppercase tracking-wide ${
        source === "manual"
          ? "bg-violet-500/15 text-violet-700 dark:text-violet-300"
          : "bg-black/[0.06] text-[var(--muted)] dark:bg-white/[0.08]"
      }`}
    >
      {label}
    </span>
  );
}

function beatIcon(kind: string) {
  if (kind === "pending_approval") {
    return <ShieldAlert size={14} className="text-amber-600 dark:text-amber-400" />;
  }
  if (kind === "intake" || kind === "live_input") {
    return <UserCircle size={14} className="text-amber-600 dark:text-amber-400" />;
  }
  if (kind === "login") return <Monitor size={14} className="text-sky-600" />;
  return <MessageSquare size={14} className="text-[var(--accent)]" />;
}

/** mm:ss — same format the narrate widget's counter shows while recording. */
function fmtMs(ms: number) {
  const s = Math.floor(Math.max(0, ms) / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

type Section = {
  key: string;
  title: string;
  beats: DemoScriptBeat[];
  collapsible: boolean;
  totalMs?: number;
};

function buildSections(
  beats: DemoScriptBeat[],
  flowTotalMs?: Record<string, number>,
): Section[] {
  const sections: Section[] = [];
  let intake: DemoScriptBeat[] = [];
  let login: DemoScriptBeat[] = [];
  let wrap: DemoScriptBeat[] = [];
  const flowMap = new Map<string, { title: string; beats: DemoScriptBeat[] }>();

  for (const beat of beats) {
    if (beat.kind === "intake") {
      intake.push(beat);
      continue;
    }
    if (beat.kind === "login") {
      login.push(beat);
      continue;
    }
    if (beat.kind === "wrap_up") {
      wrap.push(beat);
      continue;
    }
    const fid = beat.flow_id || "flow";
    const title =
      beat.flow_title ||
      (beat.kind === "speak_only" ? beat.on_screen?.replace(/^Flow \d+: /, "") : "") ||
      fid.replace(/_/g, " ");
    const entry = flowMap.get(fid) ?? { title, beats: [] };
    if (!flowMap.has(fid) && title) entry.title = title;
    entry.beats.push(beat);
    flowMap.set(fid, entry);
  }

  if (intake.length) {
    sections.push({ key: "intake", title: "Intake", beats: intake, collapsible: false });
  }
  if (login.length) {
    sections.push({ key: "login", title: "Product login", beats: login, collapsible: false });
  }
  for (const [fid, { title, beats: flowBeats }] of flowMap) {
    sections.push({
      key: `flow-${fid}`,
      title: title || fid.replace(/_/g, " "),
      beats: flowBeats,
      collapsible: flowBeats.length > 6,
      totalMs: flowTotalMs?.[fid],
    });
  }
  if (wrap.length) {
    sections.push({ key: "wrap", title: "Wrap-up", beats: wrap, collapsible: false });
  }
  return sections;
}

function BeatRow({
  beat,
  variables,
  onPatch,
}: {
  beat: DemoScriptBeat;
  variables: { alias: string; label?: string }[];
  onPatch: (id: string, patch: Partial<DemoScriptBeat>) => void;
}) {
  const phaseLabel =
    beat.kind === "intake" && beat.phase
      ? String(beat.phase).charAt(0).toUpperCase() + String(beat.phase).slice(1)
      : null;

  const isFill =
    beat.tool === "fill_field" ||
    beat.fill_mode != null ||
    beat.kind === "live_input";
  const fillMode = beat.fill_mode ?? (beat.kind === "live_input" ? "ask" : "sample");
  const otherVars = variables.filter(
    (v) => v.alias && v.alias !== (beat.field_alias || ""),
  );

  const setFillMode = (mode: "sample" | "ask" | "ref") => {
    const patch: Partial<DemoScriptBeat> = {
      fill_mode: mode,
      tool: "fill_field",
      spoken_source: "manual",
      asks_visitor: mode === "ask",
      kind: mode === "ask" ? "live_input" : "flow_step",
    };
    if (mode === "sample") {
      patch.value_ref = null;
    }
    if (mode === "ref" && !beat.value_ref && otherVars[0]) {
      patch.value_ref = otherVars[0].alias;
    }
    if (mode === "ask" && !beat.live_question) {
      const alias = (beat.field_alias || "field").replace(/_/g, " ");
      patch.live_question = `Could you share your ${alias}?`;
    }
    onPatch(beat.id, patch);
  };

  return (
    <div
      className={`rounded-lg border px-3 py-2.5 ${
        beat.asks_visitor || fillMode === "ask"
          ? "border-amber-500/40 bg-amber-500/[0.04]"
          : beat.kind === "pending_approval"
            ? "border-amber-500/30 bg-amber-500/[0.03]"
            : ""
      }`}
      style={{
        borderColor:
          beat.asks_visitor || fillMode === "ask" || beat.kind === "pending_approval"
            ? undefined
            : "var(--line)",
      }}
    >
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0">{beatIcon(beat.kind)}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {phaseLabel && (
              <span className="text-[0.68rem] font-medium text-[var(--muted)]">{phaseLabel}</span>
            )}
            {beat.on_screen && beat.kind !== "speak_only" && (
              <span className="text-[0.72rem] font-medium text-[var(--text)]">{beat.on_screen}</span>
            )}
            {(beat.asks_visitor || fillMode === "ask") && (
              <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[0.62rem] font-medium text-amber-800 dark:text-amber-300">
                Asks visitor
              </span>
            )}
            {fillMode === "ref" && beat.value_ref && (
              <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-[0.62rem] font-medium text-sky-800 dark:text-sky-300">
                Uses {beat.value_ref}
              </span>
            )}
            {beat.kind === "pending_approval" && beat.needs_approval !== false && (
              <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[0.62rem] font-medium text-amber-800 dark:text-amber-300">
                Needs approval
              </span>
            )}
            {typeof beat.speak_at_ms === "number" && (
              <span
                className="font-mono text-[0.62rem] text-[var(--muted)]"
                title="When this line is spoken during the demo"
              >
                {fmtMs(beat.speak_at_ms)}
                {typeof beat.speak_ms === "number" && beat.speak_ms > 0
                  ? ` → ${fmtMs(beat.speak_at_ms + beat.speak_ms)}`
                  : ""}
              </span>
            )}
            {typeof beat.act_at_ms === "number" && (
              <span
                className="font-mono text-[0.62rem] text-[var(--muted)]"
                title="When this step's action runs"
              >
                ⏱ {fmtMs(beat.act_at_ms)}
              </span>
            )}
            {typeof beat.speak_at_ms !== "number" &&
              typeof beat.speak_ms === "number" &&
              beat.speak_ms > 0 && (
                <span className="text-[0.62rem] text-[var(--muted)]">
                  ~{Math.round(beat.speak_ms / 1000)}s pacing
                </span>
              )}
            {sourceBadge(beat.spoken_source)}
          </div>

          {beat.kind === "pending_approval" && beat.approval_reason && (
            <p className="mt-1 text-[0.72rem] text-[var(--muted)]">{beat.approval_reason}</p>
          )}

          <label className="mt-2 block">
            <span className="mb-1 block text-[0.65rem] font-medium uppercase tracking-wide text-[var(--muted)]">
              Agent says
            </span>
            <Textarea
              value={beat.spoken ?? ""}
              onChange={(v) => {
                if (beat.kind === "pending_approval" && beat.needs_approval !== false) return;
                onPatch(beat.id, { spoken: v });
              }}
              rows={beat.spoken && beat.spoken.length > 120 ? 3 : 2}
              className={`text-[0.78rem] leading-snug ${
                beat.kind === "pending_approval" && beat.needs_approval !== false
                  ? "pointer-events-none opacity-70"
                  : ""
              }`}
            />
          </label>

          {beat.kind === "pending_approval" && beat.needs_approval !== false && (
            <p className="mt-1 text-[0.65rem] text-[var(--muted)]">
              This step still needs Client approval in the site graph before it
              runs live.
            </p>
          )}

          {beat.uses_intake_tokens && (
            <p className="mt-1 text-[0.65rem] text-[var(--muted)]">
              {"{name}"}, {"{company}"}, etc. fill in live from visitor answers.
            </p>
          )}

          {isFill && (
            <div className="mt-2 space-y-2">
              <div>
                <span className="mb-1 block text-[0.65rem] font-medium uppercase tracking-wide text-[var(--muted)]">
                  Fill with
                </span>
                <div
                  className="inline-flex flex-wrap rounded-md border p-0.5 text-[0.72rem]"
                  style={{ borderColor: "var(--line)" }}
                  role="group"
                  aria-label="Fill mode"
                >
                  {(
                    [
                      ["sample", "Sample data"],
                      ["ask", "Ask visitor"],
                      ["ref", "Use variable"],
                    ] as const
                  ).map(([mode, label]) => (
                    <button
                      key={mode}
                      type="button"
                      className={`rounded px-2 py-1 font-medium ${
                        fillMode === mode
                          ? "bg-[var(--accent)] text-white"
                          : "text-[var(--muted)] hover:text-[var(--text)]"
                      }`}
                      onClick={() => setFillMode(mode)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {fillMode === "sample" && (
                <label className="block text-[0.72rem]">
                  <span className="mb-1 block text-[var(--muted)]">Sample value</span>
                  <Textarea
                    value={beat.example_value ?? ""}
                    onChange={(v) =>
                      onPatch(beat.id, {
                        example_value: v,
                        fill_mode: "sample",
                        spoken_source: "manual",
                      })
                    }
                    rows={2}
                    className="text-[0.78rem]"
                  />
                </label>
              )}

              {fillMode === "ask" && (
                <div className="grid gap-2 sm:grid-cols-2">
                  <label className="block text-[0.72rem]">
                    <span className="mb-1 block text-[var(--muted)]">
                      Variable name ({beat.field_alias || "field"})
                    </span>
                    <Textarea
                      value={beat.field_alias ?? ""}
                      onChange={(v) =>
                        onPatch(beat.id, {
                          field_alias: v.trim().replace(/\s+/g, "_"),
                          fill_mode: "ask",
                          spoken_source: "manual",
                        })
                      }
                      rows={1}
                      className="text-[0.78rem] font-mono"
                    />
                  </label>
                  <label className="block text-[0.72rem]">
                    <span className="mb-1 block text-[var(--muted)]">Input type</span>
                    <select
                      className="w-full rounded-md border bg-transparent px-2 py-1.5 text-[0.78rem]"
                      style={{ borderColor: "var(--line)" }}
                      value={beat.input_type ?? "text"}
                      onChange={(e) =>
                        onPatch(beat.id, {
                          input_type: e.target.value,
                          fill_mode: "ask",
                          spoken_source: "manual",
                        })
                      }
                    >
                      {INPUT_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                  </label>
                  {beat.input_type === "selection" && (
                    <label className="block text-[0.72rem]">
                      <span className="mb-1 block text-[var(--muted)]">
                        Options (comma-separated)
                      </span>
                      <Textarea
                        value={(beat.input_options ?? []).join(", ")}
                        onChange={(v) =>
                          onPatch(beat.id, {
                            input_options: v.split(",").map((s) => s.trim()).filter(Boolean),
                            fill_mode: "ask",
                            spoken_source: "manual",
                          })
                        }
                        rows={2}
                        className="text-[0.78rem] font-mono"
                        placeholder="Option A, Option B, Option C"
                      />
                    </label>
                  )}
                  <label className="block text-[0.72rem]">
                    <span className="mb-1 block text-[var(--muted)]">Example if unclear</span>
                    <Textarea
                      value={beat.example_value ?? ""}
                      onChange={(v) =>
                        onPatch(beat.id, {
                          example_value: v,
                          fill_mode: "ask",
                          spoken_source: "manual",
                        })
                      }
                      rows={2}
                      className="text-[0.78rem]"
                    />
                  </label>
                  <label className="block text-[0.72rem]">
                    <span className="mb-1 block text-[var(--muted)]">Live question</span>
                    <Textarea
                      value={beat.live_question ?? ""}
                      onChange={(v) =>
                        onPatch(beat.id, {
                          live_question: v,
                          fill_mode: "ask",
                          spoken_source: "manual",
                        })
                      }
                      rows={2}
                      className="text-[0.78rem]"
                    />
                  </label>
                </div>
              )}

              {fillMode === "ref" && (
                <label className="block text-[0.72rem]">
                  <span className="mb-1 block text-[var(--muted)]">
                    Reuse answer from (earlier Ask visitor)
                  </span>
                  {otherVars.length === 0 ? (
                    <p className="text-[0.7rem] text-[var(--muted)]">
                      No ask variables yet. Mark an earlier fill as Ask visitor,
                      or order flows in the playlist so the ask runs first.
                    </p>
                  ) : (
                    <select
                      className="w-full rounded-md border bg-transparent px-2 py-1.5 text-[0.78rem]"
                      style={{ borderColor: "var(--line)" }}
                      value={beat.value_ref ?? ""}
                      onChange={(e) =>
                        onPatch(beat.id, {
                          value_ref: e.target.value,
                          fill_mode: "ref",
                          spoken_source: "manual",
                        })
                      }
                    >
                      <option value="">Select variable…</option>
                      {otherVars.map((v) => (
                        <option key={v.alias} value={v.alias}>
                          {v.label || v.alias}
                        </option>
                      ))}
                    </select>
                  )}
                </label>
              )}
            </div>
          )}

          {(isFill || beat.tool === "click_element") && (
            <label className="mt-2 flex items-center gap-2 text-[0.72rem] text-[var(--muted)]">
              <input
                type="checkbox"
                checked={!!beat.confirm_before}
                onChange={(e) =>
                  onPatch(beat.id, {
                    confirm_before: e.target.checked,
                    tool: beat.tool || (isFill ? "fill_field" : "click_element"),
                    spoken_source: "manual",
                  })
                }
              />
              <span>Confirm with visitor before this step (e.g. submit / pay)</span>
            </label>
          )}

          {beat.knowledge_refs && beat.knowledge_refs.length > 0 && (
            <details className="mt-2 text-[0.68rem] text-[var(--muted)]">
              <summary className="cursor-pointer">Knowledge ({beat.knowledge_refs.length})</summary>
              <ul className="mt-1 space-y-1">
                {beat.knowledge_refs.map((k, i) => (
                  <li key={i} className="leading-snug">
                    · {k.slice(0, 280)}
                    {k.length > 280 ? "…" : ""}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}

export function DemoScriptPanel({
  revision,
  liveRevision,
  epoch,
  onSaved,
}: {
  revision: number | null;
  liveRevision: number | null;
  epoch: number;
  onSaved?: () => void;
}) {
  const { ok, err } = useUi();
  const [data, setData] = useState<DemoScriptResponse | null>(null);
  const [beats, setBeats] = useState<DemoScriptBeat[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const d = await api.getDemoScript();
      setData(d);
      setBeats(d.beats ?? []);
      setDirty(false);
    } catch (e) {
      const msg = errText(e);
      setLoadError(msg);
      useUi.getState().err(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, epoch, revision]);

  const sections = useMemo(
    () => buildSections(beats, data?.flow_total_ms),
    [beats, data?.flow_total_ms],
  );

  const demoVariables = useMemo(() => {
    const fromBeats = beats
      .filter(
        (b) =>
          (b.fill_mode === "ask" || b.kind === "live_input") &&
          (b.field_alias || "").trim(),
      )
      .map((b) => ({
        alias: String(b.field_alias).trim(),
        label: String(b.field_alias).trim().replace(/_/g, " "),
      }));
    const seen = new Set<string>();
    const out: { alias: string; label?: string }[] = [];
    for (const v of [...(data?.demo_variables ?? []), ...fromBeats]) {
      if (!v.alias || seen.has(v.alias)) continue;
      seen.add(v.alias);
      out.push(v);
    }
    return out;
  }, [beats, data?.demo_variables]);

  useEffect(() => {
    setOpenSections((prev) => {
      const next = { ...prev };
      for (const s of sections) {
        if (!(s.key in next)) {
          next[s.key] = !s.collapsible;
        }
      }
      return next;
    });
  }, [sections]);

  const patchBeat = (id: string, patch: Partial<DemoScriptBeat>) => {
    setBeats((prev) =>
      prev.map((b) =>
        b.id === id
          ? {
              ...b,
              ...patch,
              spoken_source:
                patch.spoken_source ??
                (patch.spoken !== undefined ||
                patch.fill_mode !== undefined ||
                patch.example_value !== undefined ||
                patch.live_question !== undefined ||
                patch.value_ref !== undefined ||
                patch.input_type !== undefined ||
                patch.confirm_before !== undefined
                  ? "manual"
                  : b.spoken_source),
            }
          : b,
      ),
    );
    setDirty(true);
  };

  const save = async () => {
    setBusy(true);
    try {
      const d = await api.patchDemoScript(beats);
      setData(d);
      setBeats(d.beats ?? beats);
      setDirty(false);
      ok("Demo script saved to draft.");
      onSaved?.();
    } catch (e) {
      err(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const regenerate = async () => {
    setBusy(true);
    try {
      const d = await api.regenerateDemoScript();
      setData(d);
      setBeats(d.beats ?? []);
      setDirty(false);
      setOpenSections({});
      ok("Script regenerated from explore + graph — manual lines kept.");
      onSaved?.();
    } catch (e) {
      err(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const stats = data?.stats;
  const pct =
    stats && stats.beat_count > 0
      ? Math.round((100 * stats.spoken_count) / stats.beat_count)
      : 0;
  const genericCount = beats.filter((b) => (b.spoken ?? "").trim() === "Next step.").length;

  return (
    <Card className="mt-4">
      <CardTitle
        hint="Timeline from explore semantics, flow YAML, and intake — draft revision only."
        right={
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => void regenerate()} disabled={busy || loading}>
              <RefreshCw size={14} /> Regenerate
            </Button>
            <Button onClick={() => void save()} disabled={busy || loading || !dirty}>
              <Save size={14} /> {busy ? "Saving…" : "Save script"}
            </Button>
          </div>
        }
      >
        Demo script
        {revision !== null && (
          <span className="ml-2 font-mono text-[0.68rem] font-normal text-[var(--muted)]">
            rev {revision}
            <span
              className={
                liveRevision === revision
                  ? " text-emerald-600 dark:text-emerald-400"
                  : " text-amber-600 dark:text-amber-400"
              }
            >
              {" "}
              · {liveRevision === revision ? "live" : "draft"}
            </span>
          </span>
        )}
      </CardTitle>

      <LiveNarrationBanner />

      {loading && <BarLoader label="Composing demo script…" />}
      {!loading && loadError && (
        <Empty>
          <p className="mb-3 text-[0.82rem] text-red-600 dark:text-red-400">{loadError}</p>
          <Button variant="secondary" onClick={() => void load()}>
            <RefreshCw size={14} /> Retry
          </Button>
        </Empty>
      )}
      {!loading && !loadError && !beats.length && (
        <Empty>No beats — add flows to the playlist first.</Empty>
      )}

      {!loading && !loadError && beats.length > 0 && (
        <>
          <div
            className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2 text-[0.72rem]"
            style={{ borderColor: "var(--line)" }}
          >
            <span>
              <strong>{stats?.beat_count ?? beats.length}</strong> beats ·{" "}
              <strong>{pct}%</strong> with speech
            </span>
            {(stats?.asks_visitor_count ?? 0) > 0 && (
              <span className="text-amber-700 dark:text-amber-400">
                {stats?.asks_visitor_count} asks visitor
              </span>
            )}
            {genericCount > 0 && (
              <span className="text-amber-700 dark:text-amber-400">
                {genericCount} generic — regenerate or edit
              </span>
            )}
            {data?.sources_used && data.sources_used.length > 0 && (
              <span className="text-[var(--muted)]">
                Sources: {data.sources_used.join(", ")}
              </span>
            )}
          </div>

          <p className="mb-3 text-[0.72rem] text-[var(--muted)]">
            Playlist order = demo order. Ask-visitor variables carry forward across
            flows — use <strong>Use variable</strong> on later fills to reuse them.
          </p>

          {(() => {
            const collected = new Map<
              string,
              { type: string; at: number; where: string }
            >();
            const used = new Map<string, number[]>();
            beats.forEach((b, i) => {
              const alias = (b.field_alias || "").trim();
              if ((b.fill_mode === "ask" || b.kind === "live_input") && alias) {
                if (!collected.has(alias)) {
                  collected.set(alias, {
                    type: b.input_type || "text",
                    at: i + 1,
                    where: b.flow_id || "",
                  });
                }
              }
              if (b.fill_mode === "ref" && (b.value_ref || "").trim()) {
                const r = String(b.value_ref);
                used.set(r, [...(used.get(r) || []), i + 1]);
              }
            });
            if (collected.size === 0) return null;
            return (
              <div
                className="mb-4 rounded-lg border px-3 py-2 text-[0.72rem]"
                style={{ borderColor: "var(--line)" }}
              >
                <div className="mb-1 font-semibold text-[var(--text)]">
                  Visitor variables ({collected.size})
                </div>
                <ul className="space-y-1">
                  {[...collected.entries()].map(([alias, info]) => {
                    const reuse = used.get(alias) || [];
                    return (
                      <li key={alias} className="flex flex-wrap items-center gap-1.5">
                        <span className="font-mono text-[var(--text)]">{alias}</span>
                        <span className="rounded bg-black/[0.06] px-1 py-0.5 text-[0.6rem] uppercase text-[var(--muted)] dark:bg-white/[0.08]">
                          {info.type}
                        </span>
                        <span className="text-[var(--muted)]">
                          collected @ step {info.at}
                        </span>
                        {reuse.length > 0 && (
                          <span className="text-sky-700 dark:text-sky-300">
                            → reused @ step {reuse.join(", ")}
                          </span>
                        )}
                        {reuse.length === 0 && (
                          <span className="text-[var(--muted)]">· not reused</span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })()}

          {data?.context && (
            <details className="mb-4 text-[0.72rem] text-[var(--muted)]">
              <summary className="cursor-pointer font-medium text-[var(--text)]">
                Persona & knowledge context
              </summary>
              <p className="mt-2 whitespace-pre-wrap leading-relaxed">{data.context}</p>
            </details>
          )}

          <div className="max-h-[32rem] space-y-2 overflow-y-auto pr-1">
            {sections.map((section) => {
              const open = openSections[section.key] ?? !section.collapsible;
              const stepBeats = section.beats.filter(
                (b) =>
                  b.kind === "flow_step" ||
                  b.kind === "live_input" ||
                  b.kind === "pending_approval",
              );
              return (
                <div
                  key={section.key}
                  className="rounded-lg border"
                  style={{ borderColor: "var(--line)" }}
                >
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-[0.78rem] font-semibold"
                    onClick={() =>
                      section.collapsible &&
                      setOpenSections((p) => ({ ...p, [section.key]: !open }))
                    }
                  >
                    {section.collapsible && (
                      <ChevronDown
                        size={14}
                        className={`shrink-0 text-[var(--muted)] transition-transform ${open ? "" : "-rotate-90"}`}
                      />
                    )}
                    <span>{section.title}</span>
                    {stepBeats.length > 0 && (
                      <span className="font-normal text-[var(--muted)]">
                        · {stepBeats.length} steps
                      </span>
                    )}
                    {typeof section.totalMs === "number" && section.totalMs > 0 && (
                      <span
                        className="font-mono text-[0.7rem] font-normal text-[var(--muted)]"
                        title="Recorded length of this flow"
                      >
                        · {fmtMs(section.totalMs)}
                      </span>
                    )}
                  </button>
                  {open && (
                    <div className="space-y-1 border-t px-2 pb-2 pt-1" style={{ borderColor: "var(--line)" }}>
                      {section.beats.map((beat) => (
                        <BeatRow
                          key={beat.id}
                          beat={beat}
                          variables={demoVariables}
                          onPatch={patchBeat}
                        />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </Card>
  );
}
