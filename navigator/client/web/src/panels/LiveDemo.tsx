import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Check, Copy, PhoneOff, Play } from "lucide-react";
import { api, type Demo } from "../lib/api";
import { rise, spring, stagger } from "../lib/motion";
import {
  BarLoader,
  Button,
  Card,
  CardTitle,
  Empty,
  Field,
  Input,
  Select,
  StatusPill,
  Textarea,
} from "../components/ui";
import { errText, useUi } from "../store";

const LINK_PENDING = "Navigator joining meeting… link unlocks when the bot is in.";

export function LiveDemo() {
  const { ok, err } = useUi();
  const [platform, setPlatform] = useState("zoom");
  const [topic, setTopic] = useState("");
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [business, setBusiness] = useState("");
  const [looking, setLooking] = useState("");

  const [demo, setDemo] = useState<Demo | null>(null);
  const [starting, setStarting] = useState(false);
  const [copied, setCopied] = useState(false);
  const demoId = demo?.demo_id ?? null;
  const listRef = useRef<HTMLUListElement>(null);

  const done = demo?.status === "finished" || demo?.status === "failed";
  const joinUrl = demo?.bot_in_meeting ? demo.meeting_url : null;

  useEffect(() => {
    if (!demoId || done) return;
    let alive = true;
    const tick = async () => {
      try {
        const d = await api.getDemo(demoId);
        if (!alive) return;
        setDemo(d);
        if (d.error) err(d.error);
      } catch (e) {
        if (alive) err(errText(e));
      }
    };
    const t = setInterval(tick, 1000);
    tick();
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [demoId, done, err]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [demo?.said.length]);

  const start = async () => {
    setStarting(true);
    try {
      const d = await api.startDemo({
        platform,
        topic: topic.trim() || undefined,
        intake: {
          name: name.trim(),
          company: company.trim(),
          business_type: business.trim(),
          looking_for: looking.trim(),
        },
      });
      setDemo(d);
      ok("Demo starting.");
    } catch (e) {
      err(errText(e));
    } finally {
      setStarting(false);
    }
  };

  const end = async () => {
    if (!demoId) return;
    try {
      const d = await api.endDemo(demoId);
      setDemo(d);
      ok("Demo ended.");
    } catch (e) {
      err(errText(e));
    }
  };

  const copy = async () => {
    if (!joinUrl) return;
    try {
      await navigator.clipboard.writeText(joinUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1300);
    } catch {
      err("Copy failed — select the link manually.");
    }
  };

  const live = !!demoId && !done;

  return (
    <motion.div
      variants={stagger()}
      initial="hidden"
      animate="show"
      className="grid grid-cols-1 gap-4 lg:grid-cols-2"
    >
      <Card>
        <CardTitle hint="Prefill what the landing page already knows so the bot needn't ask.">
          New demo
        </CardTitle>
        <div className="grid gap-x-3 sm:grid-cols-2">
          <Field label="Platform">
            <Select
              value={platform}
              onChange={setPlatform}
              options={[
                { value: "zoom", label: "Zoom" },
                { value: "google_meet", label: "Google Meet" },
              ]}
            />
          </Field>
          <Field label="Demo Title">
            <Input value={topic} onChange={setTopic} placeholder="Navigator demo — ..." />
          </Field>
        </div>
        <div className="grid gap-x-3 sm:grid-cols-2">
          <Field label="Name">
            <Input value={name} onChange={setName} placeholder="optional" />
          </Field>
          <Field label="Company">
            <Input value={company} onChange={setCompany} placeholder="optional" />
          </Field>
        </div>
        <Field label="Business type">
          <Input value={business} onChange={setBusiness} placeholder="optional" />
        </Field>
        <Field label="Looking for">
          <Textarea
            value={looking}
            onChange={setLooking}
            rows={3}
            placeholder="optional — workflow or problem"
          />
        </Field>
        <Button onClick={start} disabled={starting || live}>
          <Play size={14} strokeWidth={2.2} />
          {starting ? "Starting…" : "Start demo"}
        </Button>
      </Card>

      <Card>
        <CardTitle right={<StatusPill status={demo?.status ?? "idle"} />}>
          Active demo
        </CardTitle>

        <p className="mb-2 text-[0.74rem] font-medium tracking-wide text-[var(--muted)]">
          Join link
        </p>
        <div
          className="min-h-[46px] break-all rounded-lg border bg-black/[0.02] px-3 py-2.5 font-mono text-[0.76rem] dark:bg-black/20"
          style={{ borderColor: "var(--line)" }}
        >
          <AnimatePresence mode="wait">
            <motion.span
              key={joinUrl ?? (live ? "pending" : "idle")}
              initial={{ opacity: 0, filter: "blur(4px)" }}
              animate={{ opacity: 1, filter: "blur(0px)" }}
              exit={{ opacity: 0 }}
              transition={spring}
              className={joinUrl ? "" : "text-[var(--muted)]"}
            >
              {joinUrl ?? (live ? LINK_PENDING : "—")}
            </motion.span>
          </AnimatePresence>
        </div>

        {live && !joinUrl && (
          <div className="mt-3">
            <BarLoader label="waiting for bot to join" />
          </div>
        )}

        <div className="mt-4 flex gap-2">
          <Button variant="secondary" onClick={copy} disabled={!joinUrl}>
            {copied ? <Check size={14} /> : <Copy size={14} />}
            {copied ? "Copied" : "Copy link"}
          </Button>
          <Button variant="danger" onClick={end} disabled={!demoId || done}>
            <PhoneOff size={14} />
            End
          </Button>
        </div>

        {demo?.error && (
          <p className="mt-3 text-[0.78rem] text-red-600 dark:text-red-400">{demo.error}</p>
        )}

        {demo && (
          <div
            className="mt-4 grid grid-cols-3 gap-3 border-t pt-3 text-[0.78rem]"
            style={{ borderColor: "var(--line)" }}
          >
            {[
              ["Page", demo.page_id || "—"],
              ["Actions", String(demo.actions)],
              ["Failures", String(demo.failures)],
            ].map(([k, v]) => (
              <div key={k}>
                <p className="text-[0.7rem] text-[var(--muted)]">{k}</p>
                <p className="mt-0.5 font-mono">{v}</p>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card span="lg:col-span-2">
        <CardTitle hint="What the agent has said so far, live.">Transcript</CardTitle>
        {!demo?.said.length && <Empty>Nothing yet.</Empty>}
        <ul ref={listRef} className="max-h-72 space-y-1.5 overflow-y-auto pr-1">
          <AnimatePresence initial={false}>
            {(demo?.said ?? []).map((line, i) => (
              <motion.li
                key={`${i}-${line.slice(0, 24)}`}
                layout
                variants={rise}
                initial="hidden"
                animate="show"
                transition={spring}
                className="rounded-lg border px-3 py-2 text-[0.81rem] leading-relaxed"
                style={{ borderColor: "var(--line)" }}
              >
                {line}
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      </Card>
    </motion.div>
  );
}
