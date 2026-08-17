import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Check, Copy, ExternalLink, PhoneOff, Play, Mic, TriangleAlert } from "lucide-react";
import { api, ApiError, type AutonomyMode, type DemoReadiness, type RunEvent } from "../lib/api";
import { demoIsLive, useDemoSession } from "../lib/demoSession";
import { useExploreSession } from "../lib/exploreSession";
import { useProductData } from "../lib/productData";
import {
  clientReadinessScore,
  clientVisibleReadinessChecks,
} from "../lib/readiness";
import { rise, soft, stagger } from "../lib/motion";
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
  Switch,
  Textarea,
} from "../components/ui";
import { errText, useUi } from "../store";

const LINK_PENDING = "Creating meeting link…";

function legacyCopy(text: string): boolean {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try {
    return document.execCommand("copy");
  } finally {
    ta.remove();
  }
}

export function LiveDemo() {
  const { ok, err, setTab, setLogsSessionId } = useUi();
  const invalidate = useProductData((s) => s.invalidate);
  const demo = useDemoSession((s) => s.demo);
  const starting = useDemoSession((s) => s.starting);
  const ending = useDemoSession((s) => s.ending);
  const startSession = useDemoSession((s) => s.start);
  const endSession = useDemoSession((s) => s.end);
  const refreshActive = useDemoSession((s) => s.refreshActive);

  const [platform, setPlatform] = useState("google_meet");
  const [topic, setTopic] = useState("");
  const [autoPlay, setAutoPlay] = useState(true);
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [business, setBusiness] = useState("");
  const [looking, setLooking] = useState("");
  const [domain, setDomain] = useState("");
  const [domainPlaceholder, setDomainPlaceholder] = useState(false);
  const [savingDomain, setSavingDomain] = useState(false);
  const [autonomyMode, setAutonomyMode] = useState<AutonomyMode>("guided");
  const [savingAutonomy, setSavingAutonomy] = useState(false);
  const [readiness, setReadiness] = useState<DemoReadiness | null>(null);
  const [loadingReadiness, setLoadingReadiness] = useState(false);
  const [loginUrl, setLoginUrl] = useState("");
  const [loginUser, setLoginUser] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [hasPassword, setHasPassword] = useState(false);
  const [changingPass, setChangingPass] = useState(false);
  const [includeLogin, setIncludeLogin] = useState(false);
  const [savingLogin, setSavingLogin] = useState(false);
  const [savingIncludeLogin, setSavingIncludeLogin] = useState(false);
  const [copied, setCopied] = useState(false);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const listRef = useRef<HTMLUListElement>(null);

  const live = demoIsLive(demo);
  const done = !!demo && (demo.status === "finished" || demo.status === "failed");
  const demoId = demo?.demo_id ?? null;
  const sessionId = demo?.session_id ?? null;
  // Show the link as soon as the meeting exists. Gating on bot_in_meeting
  // deadlocks test demos: static Meet (and some Zoom joins) leave the bot in
  // the waiting room until a human opens the link and admits it.
  const joinUrl = demo?.meeting_url ?? null;
  const botReady = live && !!demo?.bot_in_meeting;
  const leaveGrace = demo?.leave_grace_remaining ?? null;
  const joinDisplay =
    live || starting ? (joinUrl ?? LINK_PENDING) : done ? "—" : (joinUrl ?? "—");
  const transcriptLines = (demo?.said ?? []).filter(
    (line) => !done || !/join link ready/i.test(line),
  );

  useEffect(() => {
    void refreshActive();
  }, [refreshActive]);

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
      try {
        const mode = await api.getAutonomyMode();
        if (!alive) return;
        setAutonomyMode(mode.mode);
      } catch (e) {
        if (alive) err(errText(e));
      }
      try {
        setLoadingReadiness(true);
        const ready = await api.getDemoReadiness("dashboard_test");
        if (!alive) return;
        setReadiness(ready);
      } catch (e) {
        if (alive) err(errText(e));
      } finally {
        if (alive) setLoadingReadiness(false);
      }
      try {
        const login = await api.getProductLogin();
        if (!alive) return;
        setLoginUrl(login.login_url || "");
        setLoginUser(login.username || "");
        setHasPassword(!!login.has_password);
        setIncludeLogin(!!login.include_login_in_default_flow);
        setChangingPass(!login.has_password);
        setLoginPass("");
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
  }, [transcriptLines.length]);

  useEffect(() => {
    if (!sessionId) {
      setEvents([]);
      return;
    }
    let alive = true;
    const tick = async () => {
      try {
        const rows = await api.runEvents(sessionId);
        if (!alive) return;
        setEvents(rows);
      } catch (e) {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) return;
      }
    };
    if (live) {
      const t = setInterval(tick, 1500);
      tick();
      return () => {
        alive = false;
        clearInterval(t);
      };
    } else {
      tick();
      return () => {
        alive = false;
      };
    }
  }, [sessionId, live]);

  const saveDomain = async () => {
    setSavingDomain(true);
    try {
      const d = await api.putProductDomain(domain.trim());
      setDomain(d.base_url);
      setDomainPlaceholder(false);
      invalidate();
      void useExploreSession.getState().syncProductUrl();
      ok("Product domain saved.");
    } catch (e) {
      err(errText(e));
    } finally {
      setSavingDomain(false);
    }
  };

  const saveAutonomy = async (mode: AutonomyMode) => {
    setSavingAutonomy(true);
    try {
      const d = await api.putAutonomyMode(mode);
      setAutonomyMode(d.mode);
      invalidate();
      const ready = await api.getDemoReadiness("dashboard_test");
      setReadiness(ready);
      ok(`Autonomy: ${d.mode}.`);
    } catch (e) {
      err(errText(e));
    } finally {
      setSavingAutonomy(false);
    }
  };

  const visibleReadinessChecks = clientVisibleReadinessChecks(
    readiness?.checks ?? [],
  );
  const blockingChecks = visibleReadinessChecks.filter((c) => c.blocking && !c.ok);
  const startBlocked = blockingChecks.length > 0;

  const saveIncludeLogin = async (enabled: boolean) => {
    if (!loginUser.trim()) {
      err("Save username first.");
      return;
    }
    setSavingIncludeLogin(true);
    try {
      const saved = await api.putProductLogin({
        login_url: loginUrl.trim(),
        username: loginUser.trim(),
        password: null,
        include_login_in_default_flow: enabled,
      });
      setIncludeLogin(!!saved.include_login_in_default_flow);
      invalidate();
      ok(
        saved.include_login_in_default_flow
          ? "Default demo will include login."
          : "Default demo skips login.",
      );
    } catch (e) {
      err(errText(e));
    } finally {
      setSavingIncludeLogin(false);
    }
  };

  const saveLogin = async () => {
    setSavingLogin(true);
    try {
      const body: {
        login_url: string;
        username: string;
        password?: string | null;
        include_login_in_default_flow: boolean;
      } = {
        login_url: loginUrl.trim(),
        username: loginUser.trim(),
        include_login_in_default_flow: includeLogin,
      };
      if (changingPass) {
        body.password = loginPass;
      } else {
        body.password = null;
      }
      const saved = await api.putProductLogin(body);
      setLoginUrl(saved.login_url || "");
      setLoginUser(saved.username || "");
      setHasPassword(!!saved.has_password);
      setIncludeLogin(!!saved.include_login_in_default_flow);
      setChangingPass(!saved.has_password);
      setLoginPass("");
      invalidate();
      void useExploreSession.getState().refresh();
      void useExploreSession.getState().syncProductUrl();
      ok("Product login saved.");
    } catch (e) {
      err(errText(e));
    } finally {
      setSavingLogin(false);
    }
  };

  const start = async () => {
    if (domainPlaceholder || !domain.trim() || /example\.com/i.test(domain)) {
      err("Set your product domain first (https://your-product.com), then Start.");
      return;
    }
    if (startBlocked) {
      err(blockingChecks[0]?.message ?? "Demo not ready — fix blocking checks first.");
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
        auto_play: autoPlay,
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
      // navigator.clipboard is undefined on insecure origins (dashboard is served
      // over plain http on the LAN), so fall back to a hidden-textarea copy.
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(joinUrl);
      } else if (!legacyCopy(joinUrl)) {
        throw new Error("copy rejected");
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1300);
    } catch {
      err("Copy failed — select the link manually.");
    }
  };

  const openLogs = () => {
    if (!sessionId) return;
    setLogsSessionId(sessionId);
    setTab("logs");
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

      <Card span="lg:col-span-2">
        <CardTitle hint="Playwright sign-in for demos. Credentials stay out of the site graph.">
          Product Login
        </CardTitle>
        <div className="grid gap-x-3 sm:grid-cols-2">
          <Field label="Login URL (optional — defaults to product URL)">
            <Input
              value={loginUrl}
              onChange={setLoginUrl}
              placeholder="https://your-product.com/login"
            />
          </Field>
          <Field label="Username / email">
            <Input
              value={loginUser}
              onChange={setLoginUser}
              placeholder="demo@your-product.com"
              autoComplete="username"
            />
          </Field>
        </div>
        <Field label="Password">
          {hasPassword && !changingPass ? (
            <div className="flex items-center gap-2">
              <Input value="••••••••••••" onChange={() => {}} disabled />
              <Button
                variant="ghost"
                onClick={() => {
                  setChangingPass(true);
                  setLoginPass("");
                }}
              >
                Change
              </Button>
            </div>
          ) : (
            <Input
              value={loginPass}
              onChange={setLoginPass}
              placeholder={hasPassword ? "enter a new password" : "password"}
              type="password"
              autoComplete="new-password"
            />
          )}
        </Field>

        <div
          className="mb-3 border-t pt-3"
          style={{ borderColor: "var(--line)" }}
        >
          <Switch
            label="Show login during demo"
            description="On: run your recorded login/onboarding flow (or a live login if none). Off: silent auto sign-in — skip login/onboarding in the playlist."
            checked={includeLogin}
            disabled={savingIncludeLogin || !loginUser.trim()}
            onChange={(v) => void saveIncludeLogin(v)}
          />
        </div>

        <div
          className="mb-3 border-t pt-3"
          style={{ borderColor: "var(--line)" }}
        >
          <p className="mb-2 text-[0.78rem] font-medium">How should your agent handle unexpected questions?</p>
          <div className="mb-3 grid gap-2 sm:grid-cols-3">
            {(
              [
                ["guided", "Guided demo", "Published flows + knowledge only. Safest."],
                ["adaptive", "Adaptive demo", "Safe live clicks when no flow matches."],
                ["explorer", "Explorer demo", "Test only — navigates freely from screen."],
              ] as const
            ).map(([value, label, desc]) => (
              <label
                key={value}
                className={`cursor-pointer rounded-lg border px-3 py-2 text-[0.76rem] ${
                  autonomyMode === value ? "border-[var(--accent)] bg-[var(--accent)]/5" : ""
                }`}
                style={{ borderColor: autonomyMode === value ? undefined : "var(--line)" }}
              >
                <input
                  type="radio"
                  name="autonomy"
                  className="mr-2"
                  checked={autonomyMode === value}
                  disabled={savingAutonomy}
                  onChange={() => void saveAutonomy(value)}
                />
                <span className="font-medium">{label}</span>
                <p className="mt-1 text-[var(--muted)]">{desc}</p>
              </label>
            ))}
          </div>
          {autonomyMode === "explorer" && (
            <p className="mb-2 text-[0.74rem] text-amber-600 dark:text-amber-400">
              Explorer is dashboard test demos only — not for your live website embed.
            </p>
          )}
        </div>

        <Button
          onClick={saveLogin}
          disabled={
            savingLogin ||
            !loginUser.trim() ||
            (changingPass && !loginPass && !hasPassword)
          }
        >
          {savingLogin ? "Saving…" : "Save product login"}
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
                {
                  value: "google_meet",
                  label: "Google Meet (new open space — recommended)",
                },
                { value: "zoom", label: "Zoom (Navigator hosts via ZAK)" },
                {
                  value: "static",
                  label: "Static Meet (.env) — you open link & admit Navigator",
                },
              ]}
            />
          </Field>
          <Field label="Demo Title">
            <Input value={topic} onChange={setTopic} placeholder="Navigator demo — ..." />
          </Field>
        </div>
        {platform === "static" && (
          <p className="mb-3 text-[0.76rem] text-amber-600 dark:text-amber-400">
            Uses NAVIGATOR_MEETING_URL. You are the host — open the join link and
            admit Navigator when Meet asks. Prefer Google Meet (new space) for
            hands-free bot-first demos.
          </p>
        )}
        {platform === "zoom" && (
          <p className="mb-3 text-[0.74rem] text-[var(--muted)]">
            Navigator hosts via ZAK (auto-tunnels :8000 if PUBLIC_BASE_URL unset).
          </p>
        )}
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
        <label className="mb-3 flex cursor-pointer items-start gap-2 text-[0.78rem] leading-snug text-[var(--muted)]">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={autoPlay}
            onChange={(e) => setAutoPlay(e.target.checked)}
          />
          <span>Auto-play: continue sequentially through all flows in the playlist</span>
        </label>

        <div
          className="mb-3 rounded-lg border px-3 py-2.5"
          style={{ borderColor: "var(--line)" }}
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-[0.78rem] font-medium">Demo readiness</p>
            {readiness && (
              <span className="text-[0.76rem] text-[var(--muted)]">
                Score {clientReadinessScore(readiness.checks)}/100
              </span>
            )}
          </div>
          {loadingReadiness && !readiness ? (
            <p className="text-[0.74rem] text-[var(--muted)]">Checking…</p>
          ) : readiness ? (
            <ul className="space-y-1 text-[0.72rem]">
              {visibleReadinessChecks.map((c) => (
                <li
                  key={c.id}
                  className={
                    c.ok
                      ? "text-emerald-700 dark:text-emerald-400"
                      : c.blocking
                        ? "text-red-700 dark:text-red-400"
                        : "text-amber-700 dark:text-amber-400"
                  }
                >
                  {c.ok ? "✓" : c.blocking ? "✕" : "!"} {c.message}
                </li>
              ))}
            </ul>
          ) : null}
          {startBlocked && (
            <p className="mt-2 flex items-center gap-1.5 text-[0.74rem] text-red-600 dark:text-red-400">
              <TriangleAlert size={14} />
              Fix blocking items before starting a test demo.
            </p>
          )}
        </div>

        <Button onClick={start} disabled={starting || live || ending || startBlocked}>
          <Play size={14} strokeWidth={2.2} />
          {starting
            ? "Starting…"
            : live
              ? "Test demo running…"
              : "Run a test demo"}
        </Button>
      </Card>

      <Card>
        <CardTitle right={<StatusPill status={demo?.status ?? "idle"} />}>
          Active demo
        </CardTitle>

        {live && (
          <p className="mb-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-[0.76rem] text-emerald-800 dark:text-emerald-300">
            Test demo running — open the join link and admit Navigator. End closes
            meeting and browser.
          </p>
        )}
        {done && (
          <p className="mb-3 text-[0.76rem] text-[var(--muted)]">
            Demo finished. Start a new test demo when ready.
          </p>
        )}

        <p className="mb-2 text-[0.74rem] font-medium tracking-wide text-[var(--muted)]">
          Join link
        </p>
        <div
          className="min-h-[46px] break-all rounded-lg border bg-black/[0.02] px-3 py-2.5 font-mono text-[0.76rem] dark:bg-black/20"
          style={{ borderColor: "var(--line)" }}
        >
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.span
              key={joinDisplay}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={soft}
              className={joinDisplay !== "—" && joinDisplay !== LINK_PENDING ? "" : "text-[var(--muted)]"}
            >
              {joinDisplay}
            </motion.span>
          </AnimatePresence>
        </div>

        {live && joinUrl && !botReady && (
          <div className="mt-3">
            <BarLoader label="Navigator joining — open the link; admit the bot if asked" />
          </div>
        )}
        {live && botReady && (
          <p className="mt-2 text-[0.74rem] text-emerald-700 dark:text-emerald-400">
            Navigator is in the meeting.
          </p>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={copy} disabled={!joinUrl || !live}>
            {copied ? <Check size={14} /> : <Copy size={14} />}
            {copied ? "Copied" : "Copy link"}
          </Button>
          <Button variant="danger" onClick={end} disabled={!demoId || (!live && done) || ending}>
            <PhoneOff size={14} />
            {ending ? "Ending…" : "End"}
          </Button>
          {live && leaveGrace !== null && (
            <span
              aria-live="polite"
              className="tabular-nums text-[0.74rem] text-amber-700 dark:text-amber-300"
            >
              Ends in {leaveGrace}s
            </span>
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

        <div
          className="mt-4 border-t pt-4"
          style={{ borderColor: "var(--line)" }}
        >
          <div className="mb-2 flex items-start justify-between gap-2">
            <div>
              <p className="text-[0.78rem] font-medium tracking-tight">Live log</p>
              <p className="mt-0.5 text-[0.68rem] text-[var(--muted)]">
                Last ~20 ActionLog events (client only)
              </p>
            </div>
            {sessionId && (
              <button
                type="button"
                onClick={openLogs}
                title="Open full log in Logs"
                aria-label="Open full log in Logs"
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border text-[var(--muted)] transition hover:bg-black/[0.04] hover:text-[var(--text)] dark:hover:bg-white/[0.06]"
                style={{ borderColor: "var(--line)" }}
              >
                <ExternalLink size={15} strokeWidth={2} />
              </button>
            )}
          </div>
          {!events.length && (
            <Empty>{live ? "Waiting for actions…" : "Nothing yet."}</Empty>
          )}
          {events.length > 0 && (
            <div className="terminal-log flex max-h-56 flex-col gap-1 overflow-y-auto rounded-lg bg-[#0d1117] p-4 font-mono text-[0.72rem] leading-relaxed shadow-inner">
              {live && (
                <div className="mb-2 flex items-center gap-2 text-emerald-400">
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
                  </span>
                  Streaming — {events.length} events
                </div>
              )}
              {events.map((ev) => {
                const fail = !ev.actual_result?.ok || (ev.verify && !ev.verify.passed);
                const sel = typeof ev.tool_call?.selector === "string" ? ev.tool_call.selector : "";
                const time = ev.timestamp
                  ? new Date(ev.timestamp).toLocaleTimeString([], { hour12: false })
                  : "";
                return (
                  <div
                    key={ev.call_id}
                    className="py-0.5 opacity-90 transition-opacity hover:opacity-100"
                  >
                    <span className="text-slate-500 mr-2">{time}</span>
                    <span className="text-cyan-400 font-medium">{ev.tool_call?.tool ?? "?"}</span>
                    <span className="text-slate-400 mx-2">·</span>
                    <span className="text-slate-200">{ev.page}</span>
                    <span className="text-slate-400 mx-2">·</span>
                    <span
                      className={
                        fail ? "text-red-400 font-semibold" : "text-emerald-400 font-semibold"
                      }
                    >
                      {fail ? "FAIL" : "OK"}
                    </span>
                    {sel && (
                      <>
                        <span className="text-slate-500 mx-2">→</span>
                        <span className="text-slate-300">{sel}</span>
                      </>
                    )}
                    {ev.actual_result?.detail && (
                      <div className="mt-0.5 pl-[4.5rem] text-[0.68rem] text-slate-400">
                        {ev.actual_result.detail}
                      </div>
                    )}
                  </div>
                );
              })}
              {!live && events.length > 0 && (
                <div className="mt-2 border-t border-white/10 pt-2 text-emerald-500">
                  Demo completed — {events.length} actions, {demo?.failures ?? 0} failures.
                </div>
              )}
            </div>
          )}
        </div>
      </Card>

      <Card span="lg:col-span-2">
        <CardTitle hint="What the agent has said so far, live.">Transcript</CardTitle>
        {!transcriptLines.length && <Empty>Nothing yet.</Empty>}
        <ul ref={listRef} className="max-h-72 space-y-1.5 overflow-y-auto pr-1">
          <AnimatePresence initial={false}>
            {transcriptLines.map((line, i) => (
              <motion.li
                key={`${i}-${line.slice(0, 24)}`}
                variants={rise}
                initial="hidden"
                animate="show"
                className="rounded-lg border bg-black/[0.015] px-4 py-2.5 text-[0.81rem] leading-relaxed dark:bg-white/[0.02]"
                style={{ borderColor: "var(--line)" }}
              >
                <div className="mb-1 flex items-center gap-2 text-[0.68rem] font-medium text-[var(--muted)]">
                  <Mic size={11} className="text-[var(--accent)]" />
                  <span>Agent</span>
                </div>
                <div className="pl-4">{line}</div>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      </Card>
    </motion.div>
  );
}
