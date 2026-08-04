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
import { errText, useUi } from "../store";

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
  const { ok, err } = useUi();
  const epoch = useProductData((s) => s.epoch);
  const invalidate = useProductData((s) => s.invalidate);
  const [yaml, setYaml] = useState<string | null>(null);
  const [revision, setRevision] = useState<number | null>(null);
  const [liveRevision, setLiveRevision] = useState<number | null>(null);
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [fullScreen, setFullScreen] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.getSiteGraph();
      setYaml(d.yaml ?? "");
      setRevision(d.revision);
      setLiveRevision(d.published_revision);
    } catch (e) {
      err(errText(e));
    }
  }, [err]);

  useEffect(() => {
    void load();
  }, [load, epoch]);

  const save = async () => {
    if (yaml === null) return;
    try {
      const d = await api.putSiteGraph(yaml);
      setRevision(d.revision);
      invalidate();
      ok(`Draft saved — revision ${d.revision}. Publish to make it live.`);
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
      <Card className={fullScreen ? "flex-1 flex flex-col min-h-0" : ""}>
        <CardTitle
          hint="Your product's pages, selectors, and flows. Saving creates a draft — visitors keep seeing the published revision until you publish."
          right={
            <div className="flex items-center gap-3">
              {revision !== null && (
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
              <Button onClick={save} disabled={yaml === null}>
                <Save size={14} /> Save draft
              </Button>
              <Button onClick={() => setConfirmPublish(true)} disabled={liveRevision === revision}>
                <Save size={14} /> Publish
              </Button>
            </div>
          }
        >
          Site graph (YAML)
        </CardTitle>
        {yaml === null ? (
          <BarLoader label="Loading site graph…" />
        ) : (
          <div className={`relative ${fullScreen ? "flex-1 flex flex-col min-h-0" : ""}`}>
            <Textarea value={yaml} onChange={setYaml} rows={fullScreen ? 30 : 22} mono placeholder="version: 1" className={fullScreen ? "flex-1 resize-none h-full min-h-0 font-mono text-[0.8rem]" : "font-mono text-[0.8rem]"} />
            <div className="mt-2 flex items-center justify-between text-[0.7rem] text-[var(--muted)]">
              <span>{yaml.split("\n").length} lines</span>
              <span>{yaml.length} chars</span>
            </div>
          </div>
        )}
      </Card>
      {confirmPublish && (
        <ConfirmDialog
          title="Publish revision?"
          message={`This will make revision ${revision} live for all End User visitors. Current live revision is ${liveRevision}. Continue?`}
          onConfirm={publish}
          onCancel={() => setConfirmPublish(false)}
        />
      )}
    </motion.div>
  );
}

export function Knowledge() {
  const { ok, err } = useUi();
  const epoch = useProductData((s) => s.epoch);
  const invalidate = useProductData((s) => s.invalidate);
  const [md, setMd] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.getKnowledge();
      setMd(d.markdown ?? "");
    } catch (e) {
      err(errText(e));
    }
  }, [err]);

  useEffect(() => {
    void load();
  }, [load, epoch]);

  const save = async () => {
    if (md === null) return;
    try {
      await api.putKnowledge(md);
      invalidate();
      ok("Knowledge saved and indexed.");
    } catch (e) {
      err(errText(e));
    }
  };

  return (
    <motion.div variants={stagger()} initial="hidden" animate="show">
      <Card>
        <CardTitle
          hint="Markdown knowledge base — how the bot should talk about your product."
          right={
            <Button onClick={save} disabled={md === null}>
              <Save size={14} /> Save
            </Button>
          }
        >
          Knowledge
        </CardTitle>
        {md === null ? (
          <BarLoader label="Loading knowledge…" />
        ) : (
          <Textarea value={md} onChange={setMd} rows={20} mono placeholder="# Knowledge" />
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
