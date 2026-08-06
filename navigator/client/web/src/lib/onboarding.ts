/** Client setup checklist + localStorage flags for onboarding wizard. */

import { api, type BioField, type UserPreferences } from "./api";

const LS_PENDING = "nav-onboarding-pending";
const LS_SIGNUP_COMPANY = "nav-signup-company";

/** Cached server prefs — refreshed after login. */
let _userPrefs: UserPreferences | null = null;

export type { UserPreferences };

export type OnboardingItemId =
  | "product_url"
  | "company_name"
  | "about"
  | "referral"
  | "knowledge"
  | "login";

export type OnboardingItem = {
  id: OnboardingItemId;
  label: string;
  done: boolean;
  optional?: boolean;
};

export type OnboardingProgress = {
  items: OnboardingItem[];
  doneCount: number;
  total: number;
  percent: number;
  complete: boolean;
};

export function markSignupPending(companyName: string) {
  localStorage.setItem(LS_PENDING, "1");
  if (companyName.trim()) {
    localStorage.setItem(LS_SIGNUP_COMPANY, companyName.trim());
  }
}

export function clearSignupPending() {
  localStorage.removeItem(LS_PENDING);
}

export function isSignupPending(): boolean {
  return localStorage.getItem(LS_PENDING) === "1";
}

export function signupCompanyPrefill(): string {
  return localStorage.getItem(LS_SIGNUP_COMPANY) || "";
}

export function isOnboardingCardHidden(): boolean {
  return _userPrefs?.hide_get_started_card ?? false;
}

export async function loadUserPreferences(): Promise<UserPreferences> {
  try {
    _userPrefs = await api.getUserPreferences();
  } catch {
    _userPrefs = {
      hide_get_started_card: false,
      onboarding_wizard_dismissed: false,
      onboarding_wizard_completed: false,
    };
  }
  return _userPrefs;
}

export function cachedUserPreferences(): UserPreferences | null {
  return _userPrefs;
}

export async function hideOnboardingCard(): Promise<void> {
  _userPrefs = await api.putUserPreferences({ hide_get_started_card: true });
}

export async function showOnboardingCard(): Promise<void> {
  _userPrefs = await api.putUserPreferences({ hide_get_started_card: false });
}

export async function dismissOnboardingWizard(): Promise<void> {
  _userPrefs = await api.putUserPreferences({
    onboarding_wizard_dismissed: true,
    hide_get_started_card: true,
  });
}

export async function completeOnboardingWizard(): Promise<void> {
  _userPrefs = await api.putUserPreferences({
    onboarding_wizard_completed: true,
    hide_get_started_card: true,
  });
}

export function shouldAutoOpenWizard(): boolean {
  if (!isSignupPending()) return false;
  const p = _userPrefs;
  if (p?.onboarding_wizard_dismissed || p?.onboarding_wizard_completed) return false;
  return true;
}

function bioMap(fields: BioField[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of fields) {
    const k = (f.key || "").trim();
    if (k) out[k] = (f.value || "").trim();
  }
  return out;
}

export async function loadOnboardingProgress(): Promise<OnboardingProgress> {
  const [domain, bio, knowledge, login] = await Promise.all([
    api.getProductDomain().catch(() => ({ base_url: "", placeholder: true })),
    api.getBio().catch(() => ({ fields: [] as BioField[] })),
    api.getKnowledge().catch(() => ({ markdown: "" })),
    api.getProductLogin().catch(() => ({
      login_url: "",
      username: "",
      has_password: false,
    })),
  ]);

  const b = bioMap(bio.fields || []);
  const domainOk =
    !!(domain.base_url || "").trim() && !domain.placeholder;
  const knowledgeOk = !!(knowledge.markdown || "").trim();
  const loginOk =
    !!(login.login_url || "").trim() && !!(login.username || "").trim();

  const items: OnboardingItem[] = [
    { id: "product_url", label: "Product URL", done: domainOk },
    {
      id: "company_name",
      label: "Company name",
      done: !!(b.company_name || "").trim(),
    },
    { id: "about", label: "About / one-liner", done: !!(b.about || "").trim() },
    {
      id: "referral",
      label: "How you found us",
      done: !!(b.referral_source || "").trim(),
    },
    { id: "knowledge", label: "Knowledge blurb", done: knowledgeOk, optional: true },
    {
      id: "login",
      label: "Product login",
      done: loginOk,
      optional: true,
    },
  ];

  const doneCount = items.filter((i) => i.done).length;
  const total = items.length;
  const percent = total === 0 ? 0 : Math.round((100 * doneCount) / total);
  return {
    items,
    doneCount,
    total,
    percent,
    complete: doneCount >= total,
  };
}

/** Merge one or more bio fields into the saved bio list. */
export async function patchBioFields(
  patch: Array<{ key: string; label: string; value: string }>,
): Promise<void> {
  const cur = await api.getBio().catch(() => ({ fields: [] as BioField[] }));
  const byKey = new Map<string, BioField>();
  for (const f of cur.fields || []) {
    const k = (f.key || "").trim();
    if (k) byKey.set(k, { ...f, key: k });
  }
  for (const p of patch) {
    const key = p.key.trim();
    if (!key) continue;
    const prev = byKey.get(key);
    byKey.set(key, {
      key,
      label: p.label || prev?.label || key,
      value: p.value,
    });
  }
  await api.putBio([...byKey.values()]);
}
