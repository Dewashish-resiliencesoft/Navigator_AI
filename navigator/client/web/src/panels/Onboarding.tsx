import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Chrome,
  Linkedin,
  Loader2,
  MessageSquare,
  Podcast,
  Search,
  Users,
  Youtube,
  type LucideIcon,
} from "lucide-react";
import { api } from "../lib/api";
import {
  clearSignupPending,
  completeOnboardingWizard,
  dismissOnboardingWizard,
  patchBioFields,
  signupCompanyPrefill,
  type OnboardingItemId,
  type OnboardingProgress,
} from "../lib/onboarding";
import { useOnboardingProgress } from "../lib/useOnboardingProgress";
import { useExploreSession } from "../lib/exploreSession";
import {
  productExploreIsLive,
  productExplorePct,
  useProductExploreSession,
} from "../lib/productExploreSession";
import { useProductData } from "../lib/productData";
import { soft } from "../lib/motion";
import { Button, Field, Input, Textarea } from "../components/ui";
import { StatusChecklist } from "../components/StatusChecklist";
import { errText, useUi } from "../store";

type StepId =
  | "explore_intro"
  | "explore_setup"
  | "explore_running"
  | "product_url"
  | "company_name"
  | "about"
  | "referral"
  | "login_choice"
  | "login_fields"
  | "knowledge";

/** Explore path first; manual bio steps only if skip / fail. */
const STEPS: StepId[] = [
  "explore_intro",
  "explore_setup",
  "explore_running",
  "product_url",
  "company_name",
  "about",
  "referral",
  "login_choice",
  "login_fields",
  "knowledge",
];

const REFERRALS: Array<{
  id: string;
  label: string;
  Icon: LucideIcon;
}> = [
  { id: "friend", label: "Friend", Icon: Users },
  { id: "youtube", label: "YouTube", Icon: Youtube },
  { id: "google", label: "Google", Icon: Chrome },
  { id: "linkedin", label: "LinkedIn", Icon: Linkedin },
  { id: "podcast", label: "Podcast", Icon: Podcast },
  { id: "other", label: "Other", Icon: MessageSquare },
];

function stepIndex(id: StepId): number {
  return STEPS.indexOf(id);
}

function referralValue(id: string, other: string): string {
  if (id === "other") return other.trim();
  return REFERRALS.find((r) => r.id === id)?.label || id;
}

export function OnboardingWizard({
  onClose,
  startAt,
  onFullyComplete,
}: {
  onClose: () => void;
  /** Jump to first incomplete checklist item when resuming. */
  startAt?: OnboardingItemId | null;
  /** Checklist 100% after the user finishes the wizard (last Continue / Finish). */
  onFullyComplete?: () => void;
}) {
  const { ok, err } = useUi();
  const invalidate = useProductData((s) => s.invalidate);
  const syncProductUrl = useExploreSession((s) => s.syncProductUrl);

  const exploreStatus = useProductExploreSession((s) => s.status);
  const exploreStarting = useProductExploreSession((s) => s.starting);
  const startExplore = useProductExploreSession((s) => s.start);
  const ackExplore = useProductExploreSession((s) => s.ack);
  const exploreLive = productExploreIsLive(exploreStatus);
  const explorePct = productExplorePct(exploreStatus);

  const [step, setStep] = useState<StepId>("explore_intro");
  const [busy, setBusy] = useState(false);
  const [wantLogin, setWantLogin] = useState<boolean | null>(null);

  const [productUrl, setProductUrl] = useState("");
  const [companyName, setCompanyName] = useState(signupCompanyPrefill());
  const [about, setAbout] = useState("");
  const [referralId, setReferralId] = useState("");
  const [referralOther, setReferralOther] = useState("");
  const [loginUrl, setLoginUrl] = useState("");
  const [loginUser, setLoginUser] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [knowledge, setKnowledge] = useState("");
  const { progress, refresh } = useOnboardingProgress();
  const progressPct = progress?.percent ?? 0;
  const exploreFinishLock = useRef(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [domain, bio, login, know] = await Promise.all([
          api.getProductDomain(),
          api.getBio(),
          api.getProductLogin(),
          api.getKnowledge(),
        ]);
        if (!alive) return;
        if (domain.base_url && !domain.placeholder) setProductUrl(domain.base_url);
        const fields = bio.fields || [];
        const by: Record<string, string> = {};
        for (const f of fields) {
          if (f.key) by[f.key] = f.value || "";
        }
        if (by.company_name) setCompanyName(by.company_name);
        else if (!companyName) setCompanyName(signupCompanyPrefill());
        if (by.about) setAbout(by.about);
        if (by.referral_source) {
          const known = REFERRALS.find(
            (r) =>
              r.id !== "other" &&
              by.referral_source.toLowerCase().includes(r.label.toLowerCase()),
          );
          if (known) setReferralId(known.id);
          else {
            setReferralId("other");
            setReferralOther(by.referral_source);
          }
        }
        if (login.login_url) {
          setLoginUrl(login.login_url);
          setWantLogin(true);
        }
        if (login.username) setLoginUser(login.username);
        if (know.markdown) setKnowledge(know.markdown);

        if (startAt) {
          const map: Partial<Record<OnboardingItemId, StepId>> = {
            product_url: "explore_setup",
            company_name: "company_name",
            about: "about",
            referral: "explore_setup",
            knowledge: "knowledge",
            login: "explore_setup",
          };
          setStep(map[startAt] || "explore_intro");
        }
      } catch {
        /* empty form ok */
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startAt]);

  // Explore success inside wizard → celebrate + close (once).
  useEffect(() => {
    if (step !== "explore_running") return;
    if (exploreFinishLock.current) return;
    if (exploreStatus.phase === "done" && !exploreStatus.error && !exploreLive) {
      exploreFinishLock.current = true;
      let cancelled = false;
      (async () => {
        try {
          await ackExplore();
          invalidate();
          clearSignupPending();
          await completeOnboardingWizard();
          if (cancelled) return;
          onFullyComplete?.();
          onClose();
        } catch (e) {
          exploreFinishLock.current = false;
          if (!cancelled) err(errText(e));
        }
      })();
      return () => {
        cancelled = true;
      };
    }
    if (exploreStatus.phase === "error" || exploreStatus.error) {
      err(exploreStatus.error || "Product Explore failed — continue setup manually.");
      setStep("company_name");
    }
  }, [
    step,
    exploreStatus.phase,
    exploreStatus.error,
    exploreLive,
    ackExplore,
    invalidate,
    onFullyComplete,
    onClose,
    err,
  ]);

  const finish = (latest?: OnboardingProgress | null) => {
    clearSignupPending();
    invalidate();
    void syncProductUrl();
    const p = latest ?? progress;
    if (p?.complete) {
      void completeOnboardingWizard();
      onFullyComplete?.();
    } else {
      ok("Setup saved — you can finish anything left from Get started.");
    }
    onClose();
  };

  const skipAll = () => {
    clearSignupPending();
    void dismissOnboardingWizard();
    ok("Setup skipped — reopen from Overview or sidebar anytime.");
    onClose();
  };

  const skipExplore = () => {
    // Manual path: keep URL if already filled.
    setStep(productUrl.trim() ? "company_name" : "product_url");
  };

  const goNext = (latest?: OnboardingProgress | null) => {
    const i = stepIndex(step);
    if (step === "login_choice" && wantLogin === false) {
      setStep("knowledge");
      return;
    }
    if (step === "explore_intro") {
      setStep("explore_setup");
      return;
    }
    if (i >= STEPS.length - 1) {
      finish(latest);
      return;
    }
    setStep(STEPS[i + 1]);
  };

  const saveReferralIfAny = async () => {
    if (!referralId) return;
    const value = referralValue(referralId, referralOther);
    if (!value) return;
    await patchBioFields([
      { key: "referral_source", label: "How you heard about us", value },
    ]);
  };

  const runExploreNow = async () => {
    setBusy(true);
    try {
      const url = productUrl.trim();
      if (!url) throw new Error("Enter your product URL (https://…).");
      if (!referralId) throw new Error("Pick how you heard about us.");
      const ref = referralValue(referralId, referralOther);
      if (!ref) throw new Error("Tell us how you heard about us.");
      if (wantLogin === true) {
        if (!loginUrl.trim() || !loginUser.trim()) {
          throw new Error("Login URL and username required, or turn off login.");
        }
      }

      await api.putProductDomain(url);
      void syncProductUrl();
      await saveReferralIfAny();
      if (wantLogin === true) {
        await api.putProductLogin({
          login_url: loginUrl.trim(),
          username: loginUser.trim(),
          password: loginPass || null,
          include_login_in_default_flow: true,
        });
      }
      invalidate();
      setStep("explore_running");
      await startExplore(url);
    } catch (e) {
      err(errText(e));
      setStep("explore_setup");
    } finally {
      setBusy(false);
    }
  };

  const saveCurrent = async () => {
    if (step === "explore_intro") {
      setStep("explore_setup");
      return;
    }
    if (step === "explore_setup") {
      await runExploreNow();
      return;
    }
    if (step === "explore_running") return;

    setBusy(true);
    try {
      if (step === "product_url") {
        const url = productUrl.trim();
        if (!url) throw new Error("Enter your product URL (https://…).");
        await api.putProductDomain(url);
        void syncProductUrl();
      } else if (step === "company_name") {
        const name = companyName.trim();
        if (!name) throw new Error("Enter your company name.");
        await patchBioFields([
          { key: "company_name", label: "Company name", value: name },
        ]);
      } else if (step === "about") {
        const text = about.trim();
        if (!text) throw new Error("Add a short about / one-liner.");
        await patchBioFields([{ key: "about", label: "About", value: text }]);
      } else if (step === "referral") {
        if (!referralId) throw new Error("Pick how you heard about us.");
        const value = referralValue(referralId, referralOther);
        if (!value) throw new Error("Tell us how you heard about us.");
        await patchBioFields([
          { key: "referral_source", label: "How you heard about us", value },
        ]);
      } else if (step === "login_choice") {
        if (wantLogin === null) {
          throw new Error("Choose Yes or Skip for now.");
        }
      } else if (step === "login_fields") {
        if (!loginUrl.trim() || !loginUser.trim()) {
          throw new Error("Login URL and username are required (or go back and Skip).");
        }
        await api.putProductLogin({
          login_url: loginUrl.trim(),
          username: loginUser.trim(),
          password: loginPass || null,
          include_login_in_default_flow: true,
        });
      } else if (step === "knowledge") {
        const md = knowledge.trim();
        if (md) await api.putKnowledge(md);
      }
      invalidate();
      const updated = await refresh();
      goNext(updated);
    } catch (e) {
      err(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const skipStep = () => {
    if (step === "explore_intro" || step === "explore_setup") {
      skipExplore();
      return;
    }
    if (step === "login_choice" || step === "login_fields") {
      setWantLogin(false);
      setStep("knowledge");
      return;
    }
    if (step === "knowledge") {
      void refresh().then((updated) => goNext(updated));
      return;
    }
    if (step === "referral") {
      goNext();
      return;
    }
    goNext();
  };

  const primaryLabel =
    step === "explore_intro"
      ? "Set up Product Explore"
      : step === "explore_setup"
        ? "Explore Now"
        : step === "explore_running"
          ? "Working…"
          : step === "knowledge"
            ? "Finish"
            : "Continue";

  const exploreCanStart =
    !!productUrl.trim() &&
    !!referralId &&
    (referralId !== "other" || !!referralOther.trim()) &&
    (wantLogin !== true || (!!loginUrl.trim() && !!loginUser.trim()));

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center px-4"
      style={{
        background: "color-mix(in oklch, var(--bg) 72%, transparent)",
      }}
    >
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={soft}
        className="w-full max-w-lg rounded-2xl border p-6 shadow-xl backdrop-blur-md"
        style={{
          borderColor: "var(--line)",
          background: "color-mix(in oklch, var(--panel) 94%, transparent)",
        }}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <p className="text-[0.7rem] font-medium uppercase tracking-[0.08em] text-[var(--muted)]">
              Get started · {progressPct}%
            </p>
            <h2 className="mt-1 text-[1.15rem] font-semibold tracking-tight">
              {step.startsWith("explore") ? "Product Explore" : "Set up your demo"}
            </h2>
          </div>
          {step !== "explore_running" && (
            <Button variant="ghost" onClick={skipAll}>
              Skip setup
            </Button>
          )}
        </div>

        <div
          className="mb-5 h-1.5 overflow-hidden rounded-full"
          style={{ background: "color-mix(in oklab, var(--line) 80%, transparent)" }}
        >
          <div
            className="h-full rounded-full bg-[var(--accent)] transition-all"
            style={{
              width: `${
                step === "explore_running"
                  ? Math.max(progressPct, explorePct)
                  : progressPct
              }%`,
            }}
          />
        </div>

        <AnimatePresence mode="wait">
          <motion.div
            key={step}
            initial={{ opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -8 }}
            transition={soft}
            className="grid gap-3"
          >
            {step === "explore_intro" && (
              <>
                <p className="text-[0.8rem] text-[var(--muted)]">
                  Product Explore signs into your product, crawls key pages, checks
                  public web context, then fills Company bio and knowledge so demos
                  sound accurate — without hand-writing every field.
                </p>
                <ul className="grid gap-1.5 text-[0.75rem] text-[var(--text)]">
                  <li className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)" }}>
                    Login → crawl product → public web enrichment
                  </li>
                  <li className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)" }}>
                    Fills bio gaps + explore notes for the agent
                  </li>
                  <li className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)" }}>
                    Not the live End User walkthrough — that stays on Flows
                  </li>
                </ul>
              </>
            )}

            {step === "explore_setup" && (
              <>
                <Field label="Product URL">
                  <Input
                    value={productUrl}
                    onChange={setProductUrl}
                    placeholder="https://your-product.com"
                  />
                </Field>

                <p className="text-[0.72rem] text-[var(--muted)]">
                  Why login? The demo agent needs a signed-in crawl to learn pages
                  behind auth. Public sites can skip password.
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setWantLogin(true)}
                    className={`rounded-xl border px-3 py-2.5 text-[0.78rem] font-medium ${
                      wantLogin === true
                        ? "border-[var(--accent)] bg-[var(--accent)]/10"
                        : ""
                    }`}
                    style={{ borderColor: "var(--line)" }}
                  >
                    Add login
                  </button>
                  <button
                    type="button"
                    onClick={() => setWantLogin(false)}
                    className={`rounded-xl border px-3 py-2.5 text-[0.78rem] font-medium ${
                      wantLogin === false
                        ? "border-[var(--accent)] bg-[var(--accent)]/10"
                        : ""
                    }`}
                    style={{ borderColor: "var(--line)" }}
                  >
                    Public / skip
                  </button>
                </div>
                {wantLogin === true && (
                  <>
                    <Field label="Login URL">
                      <Input
                        value={loginUrl}
                        onChange={setLoginUrl}
                        placeholder="https://…/login"
                      />
                    </Field>
                    <Field label="Username">
                      <Input value={loginUser} onChange={setLoginUser} placeholder="demo@" />
                    </Field>
                    <Field label="Password (optional if public)">
                      <Input
                        type="password"
                        value={loginPass}
                        onChange={setLoginPass}
                        placeholder="••••••••"
                        autoComplete="new-password"
                      />
                    </Field>
                  </>
                )}

                <p className="text-[0.8rem] text-[var(--muted)]">Where did you find us?</p>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {REFERRALS.map(({ id, label, Icon }) => {
                    const active = referralId === id;
                    return (
                      <button
                        key={id}
                        type="button"
                        onClick={() => setReferralId(id)}
                        className={`flex flex-col items-center gap-1.5 rounded-xl border px-2 py-3 text-[0.75rem] font-medium transition ${
                          active
                            ? "border-[var(--accent)] bg-[var(--accent)]/10 text-[var(--text)]"
                            : "text-[var(--muted)] hover:text-[var(--text)]"
                        }`}
                        style={{ borderColor: active ? undefined : "var(--line)" }}
                      >
                        <Icon size={20} strokeWidth={1.8} />
                        {label}
                      </button>
                    );
                  })}
                </div>
                {referralId === "other" && (
                  <Field label="Tell us more">
                    <Input
                      value={referralOther}
                      onChange={setReferralOther}
                      placeholder="Conference, blog, …"
                    />
                  </Field>
                )}
              </>
            )}

            {step === "explore_running" && (
              <>
                <div className="flex items-center gap-3 rounded-xl border px-3 py-3" style={{ borderColor: "var(--line)" }}>
                  <Search size={18} className="shrink-0 text-sky-600 dark:text-sky-400" />
                  <div className="min-w-0 flex-1">
                    <p className="text-[0.8rem] font-medium">Exploring your product…</p>
                    <p className="truncate text-[0.72rem] text-[var(--muted)]">
                      {exploreStatus.looking_at ||
                        exploreStatus.current_title ||
                        "Starting…"}
                    </p>
                  </div>
                  <span className="font-mono text-[0.8rem] font-semibold tabular-nums text-sky-700 dark:text-sky-300">
                    {explorePct}%
                  </span>
                </div>
                <div
                  className="h-1.5 overflow-hidden rounded-full bg-black/[0.06] dark:bg-white/[0.08]"
                  role="progressbar"
                  aria-valuenow={explorePct}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div
                    className="h-full rounded-full bg-sky-500 transition-all"
                    style={{ width: `${explorePct}%` }}
                  />
                </div>
                <StatusChecklist items={exploreStatus.artifacts ?? []} />
                {exploreStatus.error ? (
                  <p className="text-[0.72rem] text-amber-700 dark:text-amber-400" role="alert">
                    {exploreStatus.error}
                  </p>
                ) : null}
              </>
            )}

            {step === "product_url" && (
              <>
                <p className="text-[0.8rem] text-[var(--muted)]">
                  Where should Navigator open your product for demos and explore?
                </p>
                <Field label="Product URL">
                  <Input
                    value={productUrl}
                    onChange={setProductUrl}
                    placeholder="https://your-product.com"
                  />
                </Field>
              </>
            )}

            {step === "company_name" && (
              <>
                <p className="text-[0.8rem] text-[var(--muted)]">
                  Shown in bio and used when the agent talks about your company.
                </p>
                <Field label="Company name">
                  <Input
                    value={companyName}
                    onChange={setCompanyName}
                    placeholder="Your company"
                  />
                </Field>
              </>
            )}

            {step === "about" && (
              <>
                <p className="text-[0.8rem] text-[var(--muted)]">
                  One short paragraph — what you do, for whom.
                </p>
                <Field label="About / one-liner">
                  <Textarea
                    value={about}
                    onChange={setAbout}
                    rows={4}
                    placeholder="We help … with …"
                  />
                </Field>
              </>
            )}

            {step === "referral" && (
              <>
                <p className="text-[0.8rem] text-[var(--muted)]">
                  How did you hear about Navigator?
                </p>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {REFERRALS.map(({ id, label, Icon }) => {
                    const active = referralId === id;
                    return (
                      <button
                        key={id}
                        type="button"
                        onClick={() => setReferralId(id)}
                        className={`flex flex-col items-center gap-1.5 rounded-xl border px-2 py-3 text-[0.75rem] font-medium transition ${
                          active
                            ? "border-[var(--accent)] bg-[var(--accent)]/10 text-[var(--text)]"
                            : "text-[var(--muted)] hover:text-[var(--text)]"
                        }`}
                        style={{ borderColor: active ? undefined : "var(--line)" }}
                      >
                        <Icon size={20} strokeWidth={1.8} />
                        {label}
                      </button>
                    );
                  })}
                </div>
                {referralId === "other" && (
                  <Field label="Tell us more">
                    <Input
                      value={referralOther}
                      onChange={setReferralOther}
                      placeholder="Conference, blog, …"
                    />
                  </Field>
                )}
              </>
            )}

            {step === "login_choice" && (
              <>
                <p className="text-[0.8rem] text-[var(--muted)]">
                  Optional login so explore / demos can reach signed-in pages. You
                  can skip and add this later on <strong>Live demo</strong> or{" "}
                  <strong>Flows</strong>.
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setWantLogin(true)}
                    className={`rounded-xl border px-3 py-3 text-[0.8rem] font-medium ${
                      wantLogin === true
                        ? "border-[var(--accent)] bg-[var(--accent)]/10"
                        : ""
                    }`}
                    style={{ borderColor: "var(--line)" }}
                  >
                    Yes, add login
                  </button>
                  <button
                    type="button"
                    onClick={() => setWantLogin(false)}
                    className={`rounded-xl border px-3 py-3 text-[0.8rem] font-medium ${
                      wantLogin === false
                        ? "border-[var(--accent)] bg-[var(--accent)]/10"
                        : ""
                    }`}
                    style={{ borderColor: "var(--line)" }}
                  >
                    Skip for now
                  </button>
                </div>
              </>
            )}

            {step === "login_fields" && (
              <>
                <p className="text-[0.8rem] text-[var(--muted)]">
                  Demo credentials for your product (stored for this Client only).
                </p>
                <Field label="Login URL">
                  <Input
                    value={loginUrl}
                    onChange={setLoginUrl}
                    placeholder="https://…/login"
                  />
                </Field>
                <Field label="Username">
                  <Input value={loginUser} onChange={setLoginUser} placeholder="demo@" />
                </Field>
                <Field label="Password">
                  <Input
                    type="password"
                    value={loginPass}
                    onChange={setLoginPass}
                    placeholder="••••••••"
                    autoComplete="new-password"
                  />
                </Field>
              </>
            )}

            {step === "knowledge" && (
              <>
                <p className="text-[0.8rem] text-[var(--muted)]">
                  Optional — short notes the agent should know (pricing, personas,
                  FAQs). Skip if you will fill Knowledge later.
                </p>
                <Field label="Knowledge">
                  <Textarea
                    value={knowledge}
                    onChange={setKnowledge}
                    rows={5}
                    placeholder="# Product notes&#10;- …"
                  />
                </Field>
              </>
            )}
          </motion.div>
        </AnimatePresence>

        <div className="mt-6 flex flex-wrap justify-between gap-2">
          <Button
            variant="ghost"
            disabled={
              busy ||
              exploreStarting ||
              step === "explore_running" ||
              stepIndex(step) === 0
            }
            onClick={() => {
              const i = stepIndex(step);
              if (step === "knowledge" && wantLogin === false) {
                setStep("login_choice");
                return;
              }
              if (step === "company_name" || step === "product_url") {
                setStep("explore_setup");
                return;
              }
              if (i > 0) setStep(STEPS[i - 1]);
            }}
          >
            Back
          </Button>
          <div className="flex gap-2">
            {step !== "explore_running" &&
              step !== "product_url" &&
              step !== "company_name" &&
              step !== "about" && (
                <Button variant="secondary" disabled={busy} onClick={skipStep}>
                  {step === "explore_intro" || step === "explore_setup"
                    ? "Set up manually"
                    : "Skip"}
                </Button>
              )}
            {step !== "explore_running" && (
              <Button
                disabled={
                  busy ||
                  exploreStarting ||
                  (step === "explore_setup" && !exploreCanStart)
                }
                onClick={() => void saveCurrent()}
              >
                {(busy || exploreStarting) && (
                  <Loader2 size={14} className="animate-spin" />
                )}
                {primaryLabel}
              </Button>
            )}
          </div>
        </div>
      </motion.div>
    </div>
  );
}
