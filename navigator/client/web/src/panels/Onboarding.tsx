import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Chrome,
  Linkedin,
  MessageSquare,
  Podcast,
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
import { useProductData } from "../lib/productData";
import { soft } from "../lib/motion";
import { Button, Field, Input, Textarea } from "../components/ui";
import { errText, useUi } from "../store";

type StepId =
  | "product_url"
  | "company_name"
  | "about"
  | "referral"
  | "login_choice"
  | "login_fields"
  | "knowledge";

const STEPS: StepId[] = [
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

  const [step, setStep] = useState<StepId>("product_url");
  const [busy, setBusy] = useState(false);

  const [productUrl, setProductUrl] = useState("");
  const [companyName, setCompanyName] = useState(signupCompanyPrefill());
  const [about, setAbout] = useState("");
  const [referralId, setReferralId] = useState("");
  const [referralOther, setReferralOther] = useState("");
  const [wantLogin, setWantLogin] = useState<boolean | null>(null);
  const [loginUrl, setLoginUrl] = useState("");
  const [loginUser, setLoginUser] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [knowledge, setKnowledge] = useState("");
  const { progress, refresh } = useOnboardingProgress();
  const progressPct = progress?.percent ?? 0;

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
        if (login.login_url) setLoginUrl(login.login_url);
        if (login.username) setLoginUser(login.username);
        if (know.markdown) setKnowledge(know.markdown);

        if (startAt) {
          const map: Partial<Record<OnboardingItemId, StepId>> = {
            product_url: "product_url",
            company_name: "company_name",
            about: "about",
            referral: "referral",
            knowledge: "knowledge",
            login: "login_choice",
          };
          setStep(map[startAt] || "product_url");
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

  const goNext = (latest?: OnboardingProgress | null) => {
    const i = stepIndex(step);
    if (step === "login_choice" && wantLogin === false) {
      setStep("knowledge");
      return;
    }
    if (i >= STEPS.length - 1) {
      finish(latest);
      return;
    }
    setStep(STEPS[i + 1]);
  };

  const saveCurrent = async () => {
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
        const value =
          referralId === "other"
            ? referralOther.trim()
            : REFERRALS.find((r) => r.id === referralId)?.label || referralId;
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

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center px-4"
      style={{
        background:
          "color-mix(in oklch, var(--bg) 72%, transparent)",
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
              Set up your demo
            </h2>
          </div>
          <Button variant="ghost" onClick={skipAll}>
            Skip setup
          </Button>
        </div>

        <div
          className="mb-5 h-1.5 overflow-hidden rounded-full"
          style={{ background: "color-mix(in oklab, var(--line) 80%, transparent)" }}
        >
          <div
            className="h-full rounded-full bg-[var(--accent)] transition-all"
            style={{ width: `${progressPct}%` }}
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
            disabled={busy || stepIndex(step) === 0}
            onClick={() => {
              const i = stepIndex(step);
              if (step === "knowledge" && wantLogin === false) {
                setStep("login_choice");
                return;
              }
              if (i > 0) setStep(STEPS[i - 1]);
            }}
          >
            Back
          </Button>
          <div className="flex gap-2">
            {step !== "product_url" &&
              step !== "company_name" &&
              step !== "about" && (
                <Button variant="secondary" disabled={busy} onClick={skipStep}>
                  Skip
                </Button>
              )}
            <Button disabled={busy} onClick={() => void saveCurrent()}>
              {step === "knowledge" ? "Finish" : "Continue"}
            </Button>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
