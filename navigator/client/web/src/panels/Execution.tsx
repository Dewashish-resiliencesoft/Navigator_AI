/** Explore scope + pending mutating-step approvals before live demo. */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, ShieldAlert, Trash2, X } from "lucide-react";
import { parse, stringify } from "yaml";
import { api, type Flow, type PendingCorrection } from "../lib/api";
import { useExploreSession } from "../lib/exploreSession";
import { useProductData } from "../lib/productData";
import { errText, useUi } from "../store";
import { BarLoader, Button, Card, CardTitle, Empty, Field, Input } from "../components/ui";

function saveScope(key: string, values: string[]) {
  localStorage.setItem(key, JSON.stringify(values));
}

type PendingRow = {
  flow_id: string;
  page_id: string;
  flow_name: string;
  idx: number;
  alias?: string;
  selector?: string;
  reason?: string;
  approved: boolean;
};

function ChipInput({
  label,
  description,
  values,
  onChange,
  placeholder,
}: {
  label: string;
  description: string;
  values: string[];
  onChange: (v: string[]) => void;
  placeholder: string;
}) {
  const [draft, setDraft] = useState("");

  const add = () => {
    const v = draft.trim();
    if (!v || values.includes(v)) return;
    onChange([...values, v]);
    setDraft("");
  };

  return (
    <div className="mb-3">
      <Field label={label}>
        <p className="mb-2 text-[0.68rem] leading-snug text-[var(--muted)]">{description}</p>
        <div className="flex flex-wrap gap-1.5 rounded-lg border p-2" style={{ borderColor: "var(--line)" }}>
          {values.map((v) => (
            <span
              key={v}
              className="inline-flex items-center gap-1 rounded-md bg-black/[0.06] px-2 py-0.5 text-[0.72rem] dark:bg-white/[0.08]"
            >
              {v}
              <button
                type="button"
                className="text-[var(--muted)] hover:text-[var(--text)]"
                onClick={() => onChange(values.filter((x) => x !== v))}
                aria-label={`Remove ${v}`}
              >
                <X size={12} />
              </button>
            </span>
          ))}
          <Input
            value={draft}
            onChange={setDraft}
            placeholder={placeholder}
            className="min-w-[8rem] flex-1 border-0 bg-transparent px-1 py-0.5 text-[0.78rem] shadow-none focus:ring-0"
          />
          <Button variant="secondary" type="button" onClick={add} disabled={!draft.trim()}>
            Add
          </Button>
        </div>
      </Field>
    </div>
  );
}

function parsePending(yamlText: string, playlist: Flow[]): PendingRow[] {
  let doc: Record<string, unknown>;
  try {
    doc = parse(yamlText) as Record<string, unknown>;
  } catch {
    return [];
  }
  const meta = (doc._meta ?? {}) as Record<string, unknown>;
  const bucket = (meta.pending_approvals ?? {}) as Record<string, unknown>;
  const pages = (doc.pages ?? {}) as Record<string, Record<string, unknown>>;
  const byFlow = new Map(playlist.map((p) => [p.flow_id, p]));

  const out: PendingRow[] = [];
  for (const [flowId, rows] of Object.entries(bucket)) {
    if (!Array.isArray(rows)) continue;
    const pl = byFlow.get(flowId);
    const pageId = pl?.page_id ?? "dashboard";
    for (const row of rows) {
      if (!row || typeof row !== "object") continue;
      const r = row as Record<string, unknown>;
      const idx = Number(r.idx);
      if (!Number.isFinite(idx)) continue;
      if (r.approved) continue;
      out.push({
        flow_id: flowId,
        page_id: pageId,
        flow_name: pl?.name ?? flowId.replace(/_/g, " "),
        idx,
        alias: String(r.alias ?? ""),
        selector: String(r.selector ?? ""),
        reason: String(r.reason ?? ""),
        approved: Boolean(r.approved),
      });
    }
    void pages;
  }
  return out.sort((a, b) => a.flow_id.localeCompare(b.flow_id) || a.idx - b.idx);
}

function approveInYaml(yamlText: string, flowId: string, idx: number): string {
  const doc = parse(yamlText) as Record<string, unknown>;
  const meta = ((doc._meta ??= {}) as Record<string, unknown>);
  const bucket = ((meta.pending_approvals ??= {}) as Record<string, unknown>);
  const rows = bucket[flowId];
  if (!Array.isArray(rows)) return yamlText;
  for (const row of rows) {
    if (row && typeof row === "object" && Number((row as Record<string, unknown>).idx) === idx) {
      (row as Record<string, unknown>).approved = true;
    }
  }
  return stringify(doc);
}

function dropInYaml(yamlText: string, flowId: string, pageId: string, idx: number): string {
  const doc = parse(yamlText) as Record<string, unknown>;
  const pages = (doc.pages ?? {}) as Record<string, Record<string, unknown>>;
  const page = pages[pageId];
  if (!page) return yamlText;
  const flows = (page.flows ?? {}) as Record<string, unknown>;
  const steps = flows[flowId];
  if (!Array.isArray(steps) || idx < 0 || idx >= steps.length) return yamlText;
  steps.splice(idx, 1);
  flows[flowId] = steps;

  const meta = ((doc._meta ??= {}) as Record<string, unknown>);

  const narr = ((meta.narration_suggestions ?? {}) as Record<string, unknown>)[flowId];
  if (Array.isArray(narr) && idx < narr.length) narr.splice(idx, 1);

  const shiftIdxList = (section: string) => {
    const bucket = (meta[section] ?? {}) as Record<string, unknown>;
    const entry = bucket[flowId];
    if (!Array.isArray(entry)) return;
    const kept: unknown[] = [];
    for (const row of entry) {
      if (!row || typeof row !== "object") continue;
      const r = row as Record<string, unknown>;
      const i = Number(r.idx);
      if (i === idx) continue;
      if (i > idx) r.idx = i - 1;
      kept.push(r);
    }
    bucket[flowId] = kept;
    meta[section] = bucket;
  };

  shiftIdxList("pending_approvals");
  shiftIdxList("step_timing");
  // These drive timeline playback — leaving them unshifted silently pins every
  // later narration line to the wrong action.
  shiftIdxList("step_clicks");
  shiftIdxList("step_speech");
  shiftIdxList("step_mouse_paths");

  const semBucket = (meta.semantics ?? {}) as Record<string, unknown>;
  const sem = semBucket[flowId];
  if (sem && typeof sem === "object") {
    const stepsMeta = (sem as Record<string, unknown>).steps;
    if (Array.isArray(stepsMeta)) {
      const kept: unknown[] = [];
      for (const row of stepsMeta) {
        if (!row || typeof row !== "object") continue;
        const r = row as Record<string, unknown>;
        const i = Number(r.idx);
        if (i === idx) continue;
        if (i > idx) r.idx = i - 1;
        kept.push(r);
      }
      (sem as Record<string, unknown>).steps = kept;
    }
  }

  return stringify(doc);
}

export function Execution() {
  const { ok, err } = useUi();
  const epoch = useProductData((s) => s.epoch);
  const playlist = useProductData((s) => s.playlist);

  const includePaths = useExploreSession((s) => s.includePaths);
  const excludePaths = useExploreSession((s) => s.excludePaths);
  const excludeLabels = useExploreSession((s) => s.excludeLabels);
  const setIncludePaths = useExploreSession((s) => s.setIncludePaths);
  const setExcludePaths = useExploreSession((s) => s.setExcludePaths);
  const setExcludeLabels = useExploreSession((s) => s.setExcludeLabels);

  const [yaml, setYaml] = useState<string | null>(null);
  const [revision, setRevision] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [corrections, setCorrections] = useState<PendingCorrection[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [g, rows] = await Promise.all([
        api.getSiteGraph(),
        api.listPendingCorrections().catch(() => [] as PendingCorrection[]),
      ]);
      setYaml(g.yaml);
      setRevision(g.revision);
      setCorrections(rows);
    } catch (e) {
      err(errText(e));
    } finally {
      setLoading(false);
    }
  }, [err]);

  useEffect(() => {
    void load();
  }, [load, epoch]);

  const pending = useMemo(
    () => (yaml ? parsePending(yaml, playlist ?? []) : []),
    [yaml, playlist],
  );

  const saveYaml = async (next: string) => {
    setBusy(true);
    try {
      const d = await api.putSiteGraph(next);
      setYaml(next);
      setRevision(d.revision);
      ok("Draft site graph updated.");
      void useProductData.getState().refreshPlaylist();
    } catch (e) {
      err(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const approve = async (row: PendingRow) => {
    if (!yaml) return;
    await saveYaml(approveInYaml(yaml, row.flow_id, row.idx));
  };

  const drop = async (row: PendingRow) => {
    if (!yaml) return;
    await saveYaml(dropInYaml(yaml, row.flow_id, row.page_id, row.idx));
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardTitle hint="Applied on the next explore run. Empty include = whole product.">
          Explore scope
        </CardTitle>
        <div className="grid gap-4 md:grid-cols-1">
          <ChipInput
            label="Include paths"
            description="When set, explore only visits URL paths starting with these (e.g. /contacts)."
            values={includePaths}
            onChange={(v) => {
              setIncludePaths(v);
              saveScope("nav-explore-include-paths", v);
            }}
            placeholder="/contacts"
          />
          <ChipInput
            label="Exclude paths"
            description="Never navigate to paths starting with these."
            values={excludePaths}
            onChange={(v) => {
              setExcludePaths(v);
              saveScope("nav-explore-exclude-paths", v);
            }}
            placeholder="/settings"
          />
          <ChipInput
            label="Exclude labels"
            description="Skip controls whose label contains these words (e.g. Logout)."
            values={excludeLabels}
            onChange={(v) => {
              setExcludeLabels(v);
              saveScope("nav-explore-exclude-labels", v);
            }}
            placeholder="logout"
          />
        </div>
        <p className="mt-3 text-[0.72rem] text-[var(--muted)]">
          Start explore from Flows — scope travels with the session automatically.
        </p>
      </Card>

      {corrections.length > 0 && (
        <Card>
          <CardTitle hint="Agent self-corrections from explore and live-demo failures. Approve before they change how the agent demos.">
            Pending corrections ({corrections.length})
          </CardTitle>
          <ul className="mt-2 space-y-3">
            {corrections.map((row) => (
              <li
                key={row.id}
                className="rounded-lg border px-3 py-2.5"
                style={{ borderColor: "var(--line)" }}
              >
                <p className="text-[0.85rem] leading-snug">{row.rule}</p>
                <p className="mt-1 font-mono text-[0.68rem] text-[var(--muted)]">
                  {row.page} · {row.tool_call_type}
                </p>
                <div className="mt-2 flex gap-2">
                  <Button
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        await api.approveCorrection(row.id);
                        setCorrections((c) => c.filter((r) => r.id !== row.id));
                        ok("Correction approved — used on the next demo.");
                      } catch (e) {
                        err(errText(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    <Check size={14} /> Approve
                  </Button>
                  <Button
                    variant="danger"
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        await api.rejectCorrection(row.id);
                        setCorrections((c) => c.filter((r) => r.id !== row.id));
                      } catch (e) {
                        err(errText(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    <Trash2 size={14} /> Reject
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <CardTitle
          hint="Mutating steps explore recorded but did not run. Approve before live demo."
          right={
            revision !== null ? (
              <span className="font-mono text-[0.68rem] font-normal text-[var(--muted)]">
                draft rev {revision}
              </span>
            ) : null
          }
        >
          Pending approvals
        </CardTitle>
        {loading && <BarLoader label="Loading draft…" />}
        {!loading && pending.length === 0 && (
          <Empty>No mutating steps awaiting approval.</Empty>
        )}
        {!loading && pending.length > 0 && (
          <ul className="space-y-2">
            {pending.map((row) => (
              <li
                key={`${row.flow_id}:${row.idx}`}
                className="flex flex-wrap items-start gap-3 rounded-lg border px-3 py-2.5"
                style={{ borderColor: "var(--line)" }}
              >
                <ShieldAlert size={16} className="mt-0.5 shrink-0 text-amber-600" />
                <div className="min-w-0 flex-1">
                  <p className="text-[0.78rem] font-medium">
                    {row.flow_name}{" "}
                    <span className="font-normal text-[var(--muted)]">
                      · step {row.idx + 1}
                      {row.alias ? ` · ${row.alias}` : ""}
                    </span>
                  </p>
                  {row.reason && (
                    <p className="mt-0.5 text-[0.72rem] text-[var(--muted)]">{row.reason}</p>
                  )}
                  {row.selector && (
                    <p className="mt-1 font-mono text-[0.65rem] text-[var(--muted)]">
                      {row.selector}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 gap-2">
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() => void approve(row)}
                  >
                    <Check size={14} /> Approve for live demo
                  </Button>
                  <Button variant="danger" disabled={busy} onClick={() => void drop(row)}>
                    <Trash2 size={14} /> Drop step
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
