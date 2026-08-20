import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Plus, Save, Trash2, Maximize2, Minimize2 } from "lucide-react";
import { api, slugKey, type BioField } from "../lib/api";
import { useProductData } from "../lib/productData";
import { soft, stagger } from "../lib/motion";
import {
  BarLoader,
  Button,
  Card,
  CardTitle,
  ConfirmDialog,
  Empty,
  Input,
  Textarea,
} from "../components/ui";
import { ProductExplorePanel } from "../components/ProductExplorePanel";
import { DemoScriptPanel } from "../components/DemoScriptPanel";
import { errText, useUi } from "../store";
import { useProductExploreSession } from "../lib/productExploreSession";

const EXTENDED_DEFAULT_FIELDS: BioField[] = [
  { key: "company_name", label: "Company name", value: "" },
  { key: "owner", label: "Owner / leadership", value: "" },
  { key: "founded", label: "Founded year", value: "" },
  { key: "headquarters", label: "Headquarters", value: "" },
  { key: "team_size", label: "Team size", value: "" },
  { key: "website", label: "Website", value: "" },
  { key: "industry", label: "Industry", value: "" },
  { key: "products", label: "Products", value: "" },
  { key: "about", label: "What the company is about", value: "" },
  { key: "target_market", label: "Target market", value: "" },
  { key: "key_features", label: "Key features", value: "" },
  { key: "pricing_model", label: "Pricing model", value: "" },
  { key: "usp", label: "Unique selling proposition", value: "" },
  { key: "support_email", label: "Support contact", value: "" },
  { key: "social_links", label: "Social media links", value: "" },
  { key: "linkedin", label: "LinkedIn", value: "" },
  { key: "twitter", label: "Twitter / X", value: "" },
];

const BASIC_KEYS = new Set(["company_name", "owner", "founded", "headquarters", "team_size", "website", "industry"]);
const PRODUCT_KEYS = new Set(["products", "about", "target_market", "key_features", "pricing_model", "usp"]);
const CONTACT_KEYS = new Set(["support_email", "social_links", "linkedin", "twitter"]);

export function SiteGraph() {
  const tab = useUi((s) => s.tab);
  const ok = useUi((s) => s.ok);
  const err = useUi((s) => s.err);
  const coachTarget = useUi((s) => s.coach?.target);
  const epoch = useProductData((s) => s.epoch);
  const playlist = useProductData((s) => s.playlist);
  const invalidate = useProductData((s) => s.invalidate);
  const [yaml, setYaml] = useState<string | null>(null);
  const [revision, setRevision] = useState<number | null>(null);
  const [liveRevision, setLiveRevision] = useState<number | null>(null);
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [fullScreen, setFullScreen] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [view, setView] = useState<"manual" | "automated">("manual");
  const [topoYaml, setTopoYaml] = useState("");
  const [topoMeta, setTopoMeta] = useState<{ updated_at: string | null; page_count: number }>({
    updated_at: null,
    page_count: 0,
  });

  useEffect(() => {
    if (coachTarget === "graph-publish" || coachTarget === "graph-editor") {
      setView("manual");
    }
  }, [coachTarget]);

  const playlistKey = playlist
    .map((p) => `${p.order ?? 0}:${p.page_id ?? ""}:${p.flow_id ?? ""}`)
    .join("|");

  const load = useCallback(async () => {
    try {
      const d = await api.getSiteGraph();
      setYaml(d.yaml ?? "");
      setRevision(d.revision);
      setLiveRevision(d.published_revision);
      setDirty(false);
    } catch (e) {
      err(errText(e));
    }
  }, [err]);

  const loadTopo = useCallback(async () => {
    try {
      const t = await api.getProductTopology();
      setTopoYaml(t.yaml ?? "");
      setTopoMeta({ updated_at: t.updated_at, page_count: t.page_count ?? 0 });
    } catch (e) {
      err(errText(e));
    }
  }, [err]);

  // Refetch when flows mutate the draft, and whenever the Client opens this tab.
  useEffect(() => {
    if (tab !== "graph") return;
    void load();
    void loadTopo();
  }, [load, loadTopo, epoch, tab, playlistKey]);

  const save = async () => {
    if (yaml === null) return;
    if (!yaml.trim()) {
      err("Site graph YAML cannot be empty.");
      return;
    }
    try {
      const d = await api.putSiteGraph(yaml);
      setRevision(d.revision);
      invalidate();
      setDirty(false);
      ok(`Draft saved — revision ${d.revision}. Publish to make it live.`);
    } catch (e) {
      err(errText(e));
    }
  };

  const clearSiteGraph = async () => {
    setConfirmClear(false);
    try {
      const d = await api.clearSiteGraph();
      setYaml(d.yaml ?? "");
      setRevision(d.revision);
      invalidate();
      ok("Site graph and demo script cleared — record flows manually or run Product Explore for a map.");
    } catch (e) {
      err(errText(e));
    }
  };

  const publish = async () => {
    try {
      const d = await api.publishSiteGraph();
      setLiveRevision(d.published_revision);
      invalidate();
      ok(`Revision ${d.published_revision} is live for visitors.`);
      setConfirmPublish(false);
    } catch (e) {
      err(errText(e));
      setConfirmPublish(false);
    }
  };

  return (
    <motion.div variants={stagger()} initial="hidden" animate="show" className={fullScreen ? "fixed inset-4 z-50 flex flex-col" : ""}>
      <Card className={fullScreen ? "flex-1 flex flex-col min-h-0" : ""} dataCoach="graph-editor">
        <CardTitle
          hint={
            view === "manual"
              ? "Manual demo graph: pages, selectors, and recorded flows. Saving creates a draft — visitors keep the published revision until you publish."
              : "Automated product map from Product Explore — orientation for the agent. Not editable and not used as the live walkthrough."
          }
          right={
            <div className="flex items-center gap-3">
              <select
                className="rounded-lg border bg-transparent px-2 py-1.5 text-[0.78rem]"
                style={{ borderColor: "var(--line)" }}
                value={view}
                onChange={(e) => setView(e.target.value as "manual" | "automated")}
              >
                <option value="manual">Manual demo graph</option>
                <option value="automated">Automated product map</option>
              </select>
              {view === "manual" && revision !== null && (
                <span className="font-mono text-[0.72rem] rounded-full border px-2 py-0.5" style={{ borderColor: "var(--line)", backgroundColor: "var(--panel)" }}>
                  rev {revision}
                  <span className={liveRevision === revision ? "text-emerald-500 ml-1" : "text-amber-500 ml-1"}>
                    • {liveRevision === revision ? "live" : "draft"}
                  </span>
                </span>
              )}
              <Button variant="ghost" onClick={() => setFullScreen(!fullScreen)} className="px-2">
                {fullScreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
              </Button>
              {view === "manual" && (
                <>
                  <Button variant="secondary" onClick={() => setConfirmClear(true)} disabled={yaml === null}>
                    <Trash2 size={14} /> Clear all
                  </Button>
                  <Button onClick={save} disabled={yaml === null}>
                    <Save size={14} /> Save draft
                  </Button>
                  <span data-coach="graph-publish" className="inline-flex">
                    <Button onClick={() => setConfirmPublish(true)} disabled={liveRevision === revision}>
                      <Save size={14} /> Publish
                    </Button>
                  </span>
                </>
              )}
            </div>
          }
        >
          Site graph
        </CardTitle>
        {view === "automated" ? (
          <>
            <p className="mb-2 text-[0.74rem] text-[var(--muted)]">
              {topoMeta.page_count
                ? `${topoMeta.page_count} pages`
                : "No map yet"}
              {topoMeta.updated_at ? ` · updated ${topoMeta.updated_at}` : ""}
              {" · "}
              Re-run from Knowledge → Product Explore.
            </p>
            <Textarea
              value={topoYaml || "# Run Product Explore from the Knowledge tab to generate this map."}
              onChange={() => {}}
              readOnly
              rows={fullScreen ? 30 : 22}
              mono
            />
          </>
        ) : yaml === null ? (
          <BarLoader label="Loading site graph…" />
        ) : (
          <div className={`relative ${fullScreen ? "flex-1 flex flex-col min-h-0" : ""}`}>
            <Textarea
              value={yaml}
              onChange={(v) => {
                setYaml(v);
                setDirty(true);
              }}
              rows={fullScreen ? 30 : 22} mono placeholder="version: 1" className={fullScreen ? "flex-1 resize-none h-full min-h-0 font-mono text-[0.8rem]" : "font-mono text-[0.8rem]"} />
            <div className="mt-2 flex items-center justify-between text-[0.7rem] text-[var(--muted)]">
              <span>
                {yaml.split("\n").length} lines
                {dirty ? " · unsaved edits" : ""}
              </span>
              <span>{yaml.length} chars</span>
            </div>
          </div>
        )}
      </Card>
      {view === "manual" && (
        <DemoScriptPanel
          revision={revision}
          liveRevision={liveRevision}
          epoch={epoch}
          onSaved={invalidate}
        />
      )}
      {confirmPublish && (
        <ConfirmDialog
          title="Publish revision?"
          message={`This will make revision ${revision} live for all End User visitors. Current live revision is ${liveRevision}. Continue?`}
          onConfirm={publish}
          onCancel={() => setConfirmPublish(false)}
        />
      )}
      {confirmClear && (
        <ConfirmDialog
          title="Clear site graph and demo script?"
          message="Resets the draft to a minimal empty shell: no flows, no demo script. Persona and product URL stay. Record new flows manually. Publish later to make changes live."
          confirmLabel="Clear all"
          danger
          onConfirm={() => {
            void clearSiteGraph();
          }}
          onCancel={() => setConfirmClear(false)}
        />
      )}
    </motion.div>
  );
}

export function Knowledge() {
  const { ok, err, setTab } = useUi();
  const epoch = useProductData((s) => s.epoch);
  const invalidate = useProductData((s) => s.invalidate);
  const [canonical, setCanonical] = useState<string | null>(null);
  const [userMd, setUserMd] = useState("");
  const [exploreMd, setExploreMd] = useState("");
  const [mergedAt, setMergedAt] = useState<string | null>(null);
  const [pane, setPane] = useState<"canonical" | "user" | "explore">("canonical");
  const bootstrapExplore = useProductExploreSession((s) => s.bootstrap);
  const exploreActive = useProductExploreSession((s) => s.status.active);
  const exploreEpoch = useProductData((s) => s.epoch);

  const load = useCallback(async () => {
    try {
      const d = await api.getKnowledge();
      setCanonical(d.markdown ?? "");
      setUserMd(d.user_markdown ?? "");
      setExploreMd(d.explore_markdown ?? "");
      setMergedAt(d.merged_at ?? null);
    } catch (e) {
      err(errText(e));
    }
  }, [err]);

  useEffect(() => {
    bootstrapExplore();
  }, [bootstrapExplore]);

  useEffect(() => {
    void load();
  }, [load, epoch, exploreEpoch]);

  // Reload knowledge when explore finishes (invalidate bumps epoch).
  useEffect(() => {
    if (!exploreActive) void load();
  }, [exploreActive, load]);

  const saveCanonical = async () => {
    if (canonical === null) return;
    try {
      await api.putKnowledge(canonical);
      invalidate();
      ok("Knowledge saved and indexed.");
    } catch (e) {
      err(errText(e));
    }
  };

  const saveUser = async () => {
    try {
      const d = await api.putKnowledgeUser(userMd);
      setUserMd(d.user_markdown ?? userMd);
      setExploreMd(d.explore_markdown ?? exploreMd);
      setCanonical(d.markdown ?? "");
      setMergedAt(d.merged_at ?? null);
      invalidate();
      ok("User knowledge saved — merged into canonical.");
    } catch (e) {
      err(errText(e));
    }
  };

  return (
    <motion.div className="space-y-5" variants={stagger()} initial="hidden" animate="show">
      <ProductExplorePanel />
      <p className="-mt-2 text-[0.74rem] text-[var(--muted)]">
        <button
          type="button"
          className="cursor-pointer underline-offset-2 hover:underline"
          onClick={() => setTab("graph")}
        >
          View automated map on Site graph
        </button>
      </p>

      <Card>
        <CardTitle
          hint="User source + explore source auto-merge into canonical (editable). Agent reads canonical."
          right={
            <div className="flex gap-2" data-coach="knowledge-editor">
              {pane === "user" ? (
                <Button onClick={() => void saveUser()}>
                  <Save size={14} /> Save user
                </Button>
              ) : pane === "canonical" ? (
                <Button onClick={() => void saveCanonical()} disabled={canonical === null}>
                  <Save size={14} /> Save canonical
                </Button>
              ) : null}
            </div>
          }
        >
          Knowledge
        </CardTitle>
        <div className="mb-3 flex flex-wrap gap-2 text-[0.78rem]">
          {(
            [
              ["canonical", "Canonical (merged)"],
              ["user", "Your markdown"],
              ["explore", "Explore markdown"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={`rounded-lg border px-3 py-1.5 ${
                pane === id ? "border-[var(--accent)] bg-[var(--accent)]/5" : ""
              }`}
              style={{ borderColor: pane === id ? undefined : "var(--line)" }}
              onClick={() => setPane(id)}
            >
              {label}
            </button>
          ))}
        </div>
        {mergedAt && (
          <p className="mb-2 text-[0.72rem] text-[var(--muted)]">
            Last auto-merge: {mergedAt}
          </p>
        )}
        {canonical === null ? (
          <BarLoader label="Loading knowledge…" />
        ) : pane === "explore" ? (
          <Textarea value={exploreMd} onChange={() => {}} rows={18} mono readOnly placeholder="Run Product Explore to generate…" />
        ) : pane === "user" ? (
          <Textarea value={userMd} onChange={setUserMd} rows={18} mono placeholder="# Your product knowledge" />
        ) : (
          <Textarea value={canonical} onChange={setCanonical} rows={18} mono placeholder="# Knowledge" />
        )}
        {pane === "explore" && (
          <p className="mt-2 text-[0.72rem] text-[var(--muted)]">
            Explore markdown is regenerated by Product Explore (not edited here).
          </p>
        )}
      </Card>
    </motion.div>
  );
}

export function Bio() {
  const { ok, err } = useUi();
  const epoch = useProductData((s) => s.epoch);
  const invalidate = useProductData((s) => s.invalidate);
  const [fields, setFields] = useState<BioField[] | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.getBio();
      if (!d.fields || d.fields.length === 0) {
        setFields(EXTENDED_DEFAULT_FIELDS.map(f => ({ ...f })));
      } else {
        setFields(
          d.fields.map((f) => ({
            key: f.key || slugKey(f.label),
            label: f.label ?? "",
            value: f.value ?? "",
          })),
        );
      }
    } catch (e) {
      err(errText(e));
    }
  }, [err]);

  useEffect(() => {
    void load();
  }, [load, epoch]);

  const save = async () => {
    if (!fields) return;
    try {
      await api.putBio(
        fields.map((f) => ({ ...f, key: f.key || slugKey(f.label) })),
      );
      invalidate();
      await load();
      ok("Company bio saved.");
    } catch (e) {
      err(errText(e));
    }
  };

  return (
    <motion.div variants={stagger()} initial="hidden" animate="show">
      <Card>
        <CardTitle
          hint="Structured label / value pairs. The key slug is derived from the label."
          right={
            <div className="flex gap-2">
              <Button
                variant="ghost"
                onClick={() => setFields(EXTENDED_DEFAULT_FIELDS.map(f => ({ ...f })))}
              >
                Reset defaults
              </Button>
              <Button
                variant="secondary"
                onClick={() =>
                  setFields([...(fields ?? []), { key: "", label: "", value: "" }])
                }
              >
                <Plus size={14} /> Add field
              </Button>
              <Button onClick={save} disabled={!fields}>
                <Save size={14} /> Save
              </Button>
            </div>
          }
        >
          Company bio
        </CardTitle>

        {!fields && <BarLoader label="Loading bio…" />}
        {fields?.length === 0 && <Empty>No fields yet — click Add field.</Empty>}

        <div className="space-y-4">
          <AnimatePresence initial={false}>
            {fields && (
              <>
                <div className="space-y-1.5">
                  <h3 className="text-[0.72rem] font-semibold uppercase tracking-wider text-[var(--muted)] mb-2 px-1">Basic Info</h3>
                  {fields.map((f, i) => BASIC_KEYS.has(f.key) && <BioFieldRow key={i} f={f} i={i} fields={fields} setFields={setFields} />)}
                </div>
                <div className="space-y-1.5">
                  <h3 className="text-[0.72rem] font-semibold uppercase tracking-wider text-[var(--muted)] mb-2 px-1">Product Details</h3>
                  {fields.map((f, i) => PRODUCT_KEYS.has(f.key) && <BioFieldRow key={i} f={f} i={i} fields={fields} setFields={setFields} />)}
                </div>
                <div className="space-y-1.5">
                  <h3 className="text-[0.72rem] font-semibold uppercase tracking-wider text-[var(--muted)] mb-2 px-1">Contact & Social</h3>
                  {fields.map((f, i) => CONTACT_KEYS.has(f.key) && <BioFieldRow key={i} f={f} i={i} fields={fields} setFields={setFields} />)}
                </div>
                {fields.some(f => !BASIC_KEYS.has(f.key) && !PRODUCT_KEYS.has(f.key) && !CONTACT_KEYS.has(f.key)) && (
                  <div className="space-y-1.5">
                    <h3 className="text-[0.72rem] font-semibold uppercase tracking-wider text-[var(--muted)] mb-2 px-1">Other</h3>
                    {fields.map((f, i) => !BASIC_KEYS.has(f.key) && !PRODUCT_KEYS.has(f.key) && !CONTACT_KEYS.has(f.key) && <BioFieldRow key={i} f={f} i={i} fields={fields} setFields={setFields} />)}
                  </div>
                )}
              </>
            )}
          </AnimatePresence>
        </div>
      </Card>
    </motion.div>
  );
}

function BioFieldRow({ f, i, fields, setFields }: { f: BioField, i: number, fields: BioField[], setFields: (f: BioField[]) => void }) {
  const isDefault = BASIC_KEYS.has(f.key) || PRODUCT_KEYS.has(f.key) || CONTACT_KEYS.has(f.key);
  return (
    <motion.div
      layout
      transition={soft}
      exit={{ opacity: 0, x: -10 }}
      className="grid grid-cols-[1fr_1.6fr_auto] items-center gap-2"
    >
      <Input
        value={f.label}
        placeholder="Label"
        disabled={isDefault}
        onChange={(v) =>
          setFields(
            fields.map((x, n) =>
              n === i ? { ...x, label: v, key: slugKey(v) } : x,
            ),
          )
        }
      />
      {f.key === "about" || f.key === "products" || f.key === "target_market" || f.key === "usp" ? (
        <Textarea
          value={f.value}
          placeholder="Value"
          rows={2}
          onChange={(v) =>
            setFields(fields.map((x, n) => (n === i ? { ...x, value: v } : x)))
          }
        />
      ) : (
        <Input
          value={f.value}
          placeholder="Value"
          onChange={(v) =>
            setFields(fields.map((x, n) => (n === i ? { ...x, value: v } : x)))
          }
        />
      )}
      <Button
        variant="ghost"
        onClick={() => setFields(fields.filter((_, n) => n !== i))}
        className="px-1.5"
      >
        <Trash2 size={13} />
      </Button>
    </motion.div>
  );
}
