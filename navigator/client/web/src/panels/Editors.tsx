import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Plus, Save, Trash2 } from "lucide-react";
import { api, slugKey, type BioField } from "../lib/api";
import { soft, stagger } from "../lib/motion";
import {
  BarLoader,
  Button,
  Card,
  CardTitle,
  Empty,
  Input,
  Textarea,
} from "../components/ui";
import { errText, useUi } from "../store";

export function SiteGraph() {
  const { ok, err } = useUi();
  const [yaml, setYaml] = useState<string | null>(null);
  const [revision, setRevision] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.getSiteGraph();
      setYaml(d.yaml ?? "");
      setRevision(d.revision);
    } catch (e) {
      err(errText(e));
    }
  }, [err]);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    if (yaml === null) return;
    try {
      const d = await api.putSiteGraph(yaml);
      setRevision(d.revision);
      ok(`Site graph saved — revision ${d.revision}.`);
    } catch (e) {
      err(errText(e));
    }
  };

  return (
    <motion.div variants={stagger()} initial="hidden" animate="show">
      <Card>
        <CardTitle
          hint="Your product's pages, selectors, and flows. This is what the agent drives."
          right={
            <div className="flex items-center gap-3">
              {revision !== null && (
                <span className="font-mono text-[0.72rem] text-[var(--muted)]">
                  rev {revision}
                </span>
              )}
              <Button onClick={save} disabled={yaml === null}>
                <Save size={14} /> Save
              </Button>
            </div>
          }
        >
          Site graph (YAML)
        </CardTitle>
        {yaml === null ? (
          <BarLoader label="Loading site graph…" />
        ) : (
          <Textarea value={yaml} onChange={setYaml} rows={22} mono placeholder="version: 1" />
        )}
      </Card>
    </motion.div>
  );
}

export function Knowledge() {
  const { ok, err } = useUi();
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
    load();
  }, [load]);

  const save = async () => {
    if (md === null) return;
    try {
      await api.putKnowledge(md);
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
  const [fields, setFields] = useState<BioField[] | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.getBio();
      setFields(
        (d.fields ?? []).map((f) => ({
          key: f.key || slugKey(f.label),
          label: f.label ?? "",
          value: f.value ?? "",
        })),
      );
    } catch (e) {
      err(errText(e));
    }
  }, [err]);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    if (!fields) return;
    try {
      await api.putBio(
        fields.map((f) => ({ ...f, key: f.key || slugKey(f.label) })),
      );
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

        <div className="space-y-1.5">
          <AnimatePresence initial={false}>
            {fields?.map((f, i) => (
              <motion.div
                key={i}
                layout
                transition={soft}
                exit={{ opacity: 0, x: -10 }}
                className="grid grid-cols-[1fr_1.6fr_auto] items-center gap-2"
              >
                <Input
                  value={f.label}
                  placeholder="Label"
                  onChange={(v) =>
                    setFields(
                      fields.map((x, n) =>
                        n === i ? { ...x, label: v, key: slugKey(v) } : x,
                      ),
                    )
                  }
                />
                <Input
                  value={f.value}
                  placeholder="Value"
                  onChange={(v) =>
                    setFields(fields.map((x, n) => (n === i ? { ...x, value: v } : x)))
                  }
                />
                <Button
                  variant="ghost"
                  onClick={() => setFields(fields.filter((_, n) => n !== i))}
                  className="px-1.5"
                >
                  <Trash2 size={13} />
                </Button>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </Card>
    </motion.div>
  );
}
