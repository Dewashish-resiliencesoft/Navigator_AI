import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Check, Copy, PhoneOff, Play } from "lucide-react";
import { api, ApiError, type RunEvent } from "../lib/api";
import { demoIsLive, useDemoSession } from "../lib/demoSession";
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
  const { ok, err, setTab, setLogsSessionId } = useUi();
  const demo = useDemoSession((s) => s.demo);
  const starting = useDemoSession((s) => s.starting);
  const ending = useDemoSession((s) => s.ending);
  const startSession = useDemoSession((s) => s.start);
  const endSession = useDemoSession((s) => s.end);

  const [platform, setPlatform] = useState("zoom");
  const [topic, setTopic] = useState("");
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [business, setBusiness] = useState("");
  const [looking, setLooking] = useState("");
  const [domain, setDomain] = useState("");
  const [domainPlaceholder, setDomainPlaceholder] = useState(false);
  const [savingDomain, setSavingDomain] = useState(false);
  const [copied, setCopied] = useState(false);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const listRef = useRef<HTMLUListElement>(null);

  const live = demoIsLive(demo);
  const done = !!demo && (demo.status === "finished" || demo.status === "failed");
  const demoId = demo?.demo_id ?? null;
  const sessionId = demo?.session_id ?? null;
  const joinUrl = demo?.bot_in_meeting ? demo.meeting_url : null;

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const d = await api.getProductDomain();
        if (!alive) return;
        setDomain(d.base_url || "");
        setDomainPlaceholder(!!d.placeholder);
      } catch (e) {
        if (alive) err(errText(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [err]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [demo?.said.length]);

  useEffect(() => {
    if (!sessionId || !live) {
      if (!live) return;
      setEvents([]);
      return;
    }
    let alive = true;
    const tick = async () => {
      try {
        const rows = await api.runEvents(sessionId);
        if (!alive) return;
        setEvents(rows.slice(-20));
      } catch (e) {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) return;
      }
    };
    const t = setInterval(tick, 1500);
    tick();
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [sessionId, live]);

  const saveDomain = async () => {
    setSavingDomain(true);
    try {
      const d = await api.putProductDomain(domain.trim());
      setDomain(d.base_url);
      setDomainPlaceholder(false);
      ok("Product domain saved.");
    } catch (e) {
      err(errText(e));
    } finally {
      setSavingDomain(false);
    }
  };

  const start = async () => {
    if (domainPlaceholder || !domain.trim() || /example\.com/i.test(domain)) {
      err("Set your product domain first (https://your-product.com), then Start.");
      return;
    }
    if (live) {
      err("A demo is already running — end it first.");
      return;
    }
    try {
      await startSession({
        platform,
        topic: topic.trim() || undefined,
        intake: {
          name: name.trim(),
          company: company.trim(),
          business_type: business.trim(),
          looking_for: looking.trim(),
        },
      });
      ok("Demo starting.");
    } catch (e) {
      err(errText(e));
    }
  };

  const end = async () => {
    if (!demoId) return;
    try {
      await endSession(demoId);
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

  return (
    <motion.div
      variants={stagger()}
      initial="hidden"
      animate="show"
      className="grid grid-cols-1 gap-4 lg:grid-cols-2"
    >
      <Card span="lg:col-span-2">
        <CardTitle hint="Origin the agent opens and screenshares during the live demo.">
          Product domain
        </CardTitle>
        <Field label="Website URL">
          <Input
            value={domain}
            onChange={setDomain}
            placeholder="https://your-product.com"
          />
        </Field>
        {domainPlaceholder && (
          <p className="mb-3 text-[0.76rem] text-amber-600 dark:text-amber-400">
            Still on example.com — set your real product URL or screenshare stays blank.
          </p>
        )}
        <Button onClick={saveDomain} disabled={savingDomain || !domain.trim()}>
          {savingDomain ? "Saving…" : "Save domain"}
        </Button>
      </Card>

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
                { value: "google_meet", label: "Google Meet (new space)" },
                { value: "static", label: "Static Meet link (.env)" },
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
        <Button onClick={start} disabled={starting || live || ending}>
          <Play size={14} strokeWidth={2.2} />
          {starting ? "Starting…" : live ? "Demo running…" : "Start demo"}
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

        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="secondary" onClick={copy} disabled={!joinUrl}>
            {copied ? <Check size={14} /> : <Copy size={14} />}
            {copied ? "Copied" : "Copy link"}
          </Button>
          <Button variant="danger" onClick={end} disabled={!demoId || (!live && done) || ending}>
            <PhoneOff size={14} />
            {ending ? "Ending…" : "End"}
          </Button>
          {sessionId && (
            <Button
              variant="ghost"
              onClick={() => {
                setLogsSessionId(sessionId);
                setTab("logs");
              }}
            >
              Open in Logs
            </Button>
          )}
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
        <CardTitle hint="Last ~20 ActionLog events for this run (technical — client only).">
          Live log
        </CardTitle>
        {!events.length && <Empty>{live ? "Waiting for actions…" : "Nothing yet."}</Empty>}
        <ul className="max-h-48 space-y-1 overflow-y-auto font-mono text-[0.72rem] text-[var(--muted)]">
          {events.map((ev) => {
            const fail = !ev.actual_result?.ok || (ev.verify && !ev.verify.passed);
            const sel =
              typeof ev.tool_call?.selector === "string" ? ev.tool_call.selector : "";
            return (
              <li
                key={ev.call_id}
                className={fail ? "text-red-700 dark:text-red-400" : undefined}
              >
                {ev.tool_call?.tool ?? "?"} · {ev.page} · {fail ? "FAIL" : "OK"}
                {sel ? ` · ${sel}` : ""}
                {ev.actual_result?.detail ? ` · ${ev.actual_result.detail}` : ""}
              </li>
            );
          })}
        </ul>
      </Card>

      <Card span="lg:col-span-2">
        <CardTitle hint="What the agent has said so far, live.">Transcript</CardTitle>
        {!demo?.said?.length && <Empty>Nothing yet.</Empty>}
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
