import { useCallback, useEffect, useState } from "react";
import { motion } from "motion/react";
import { Save } from "lucide-react";
import { api, type AgentSettings, type SpokenLanguage, type AgentGender } from "../lib/api";
import { useProductData } from "../lib/productData";
import { stagger } from "../lib/motion";
import { BarLoader, Button, Card, CardTitle, Input, Switch } from "../components/ui";
import { errText, useUi } from "../store";

const LANG_LABELS: Record<SpokenLanguage, string> = {
  en: "English",
  hi: "Hindi",
};

export function Settings() {
  const { ok, err } = useUi();
  const epoch = useProductData((s) => s.epoch);
  const [loading, setLoading] = useState(true);
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [geminiKey, setGeminiKey] = useState("");
  const [groqKey, setGroqKey] = useState("");
  const [includeLogin, setIncludeLogin] = useState(false);
  const [loginUsername, setLoginUsername] = useState("");
  const [loginUrl, setLoginUrl] = useState("");
  const [savingLoginToggle, setSavingLoginToggle] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [d, login] = await Promise.all([
        api.getAgentSettings(),
        api.getProductLogin(),
      ]);
      setSettings(d);
      setIncludeLogin(!!login.include_login_in_default_flow);
      setLoginUsername(login.username || "");
      setLoginUrl(login.login_url || "");
      setGeminiKey("");
      setGroqKey("");
    } catch (e) {
      err(errText(e));
    } finally {
      setLoading(false);
    }
  }, [err]);

  useEffect(() => {
    void load();
  }, [load, epoch]);

  const toggleExtraLang = (lang: SpokenLanguage) => {
    if (!settings) return;
    const has = settings.extra_languages.includes(lang);
    const next = has
      ? settings.extra_languages.filter((l) => l !== lang)
      : [...settings.extra_languages, lang];
    setSettings({ ...settings, extra_languages: next.length ? next : [lang] });
  };

  const saveSettings = async () => {
    if (!settings) return;
    try {
      const d = await api.putAgentSettings({
        default_language: settings.default_language,
        extra_languages: settings.extra_languages,
        agent_gender: settings.agent_gender,
        agent_name: settings.agent_name,
        tone: settings.tone,
        gemini_voice: settings.gemini_voice,
      });
      setSettings((prev) => (prev ? { ...prev, ...d } : d));
      ok("Agent settings saved.");
    } catch (e) {
      err(errText(e));
    }
  };

  const saveKeys = async () => {
    try {
      const body: {
        gemini_api_key?: string | null;
        groq_api_key?: string | null;
      } = {};
      if (geminiKey.trim()) body.gemini_api_key = geminiKey.trim();
      if (groqKey.trim()) body.groq_api_key = groqKey.trim();
      if (!Object.keys(body).length) {
        err("Enter at least one API key to save.");
        return;
      }
      const d = await api.putAgentProviderKeys(body);
      setSettings((prev) => (prev ? { ...prev, ...d } : prev));
      setGeminiKey("");
      setGroqKey("");
      ok("Provider keys saved.");
    } catch (e) {
      err(errText(e));
    }
  };

  const saveLoginToggle = async (enabled: boolean) => {
    if (!loginUsername.trim()) {
      err("Save demo login username on Live demo first.");
      return;
    }
    setSavingLoginToggle(true);
    try {
      const saved = await api.putProductLogin({
        login_url: loginUrl.trim(),
        username: loginUsername.trim(),
        password: null,
        include_login_in_default_flow: enabled,
      });
      setIncludeLogin(!!saved.include_login_in_default_flow);
      ok(
        enabled
          ? "Login will show on screenshare before the walkthrough."
          : "Login runs silently before screen share.",
      );
    } catch (e) {
      err(errText(e));
    } finally {
      setSavingLoginToggle(false);
    }
  };

  if (loading || !settings) {
    return <BarLoader label="Loading agent settings…" />;
  }

  return (
    <motion.div className="space-y-5" variants={stagger()} initial="hidden" animate="show">
      <Card>
        <CardTitle
          hint="Voice gender, name, and tone must match — Hindi verb forms follow the persona you pick."
          right={
            <Button onClick={saveSettings}>
              <Save size={14} /> Save
            </Button>
          }
        >
          Agent persona
        </CardTitle>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block space-y-1.5 text-[0.8rem]">
            <span className="font-medium text-[var(--muted)]">Voice gender</span>
            <select
              className="w-full rounded-lg border bg-transparent px-3 py-2 text-[0.85rem]"
              style={{ borderColor: "var(--line)" }}
              value={settings.agent_gender}
              onChange={(e) =>
                setSettings({ ...settings, agent_gender: e.target.value as AgentGender })
              }
            >
              <option value="female">Female</option>
              <option value="male">Male</option>
            </select>
          </label>
          <label className="block space-y-1.5 text-[0.8rem]">
            <span className="font-medium text-[var(--muted)]">Agent display name</span>
            <Input
              value={settings.agent_name}
              onChange={(v) => setSettings({ ...settings, agent_name: v })}
              placeholder="Leave blank to use site graph persona"
            />
          </label>
          <label className="col-span-full block space-y-1.5 text-[0.8rem]">
            <span className="font-medium text-[var(--muted)]">Speaking tone</span>
            <Input
              value={settings.tone}
              onChange={(v) => setSettings({ ...settings, tone: v })}
              placeholder="e.g. friendly, concise — blank uses site graph"
            />
          </label>
        </div>
      </Card>

      <Card>
        <CardTitle hint="Default language at demo start. Extra languages allow mid-call switches.">
          Languages
        </CardTitle>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block space-y-1.5 text-[0.8rem]">
            <span className="font-medium text-[var(--muted)]">Default spoken language</span>
            <select
              className="w-full rounded-lg border bg-transparent px-3 py-2 text-[0.85rem]"
              style={{ borderColor: "var(--line)" }}
              value={settings.default_language}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  default_language: e.target.value as SpokenLanguage,
                })
              }
            >
              <option value="en">English</option>
              <option value="hi">Hindi</option>
            </select>
          </label>
          <fieldset className="space-y-2 text-[0.8rem]">
            <legend className="font-medium text-[var(--muted)]">Also speak</legend>
            {(["en", "hi"] as SpokenLanguage[]).map((lang) => (
              <label key={lang} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={settings.extra_languages.includes(lang)}
                  onChange={() => toggleExtraLang(lang)}
                />
                {LANG_LABELS[lang]}
              </label>
            ))}
          </fieldset>
        </div>
      </Card>

      <Card>
        <CardTitle hint="Meet voice is Gemini Live. Override name if needed (Sulafat, Charon).">
          Live voice
        </CardTitle>
        <label className="block space-y-1.5 text-[0.8rem]">
          <span className="font-medium text-[var(--muted)]">Gemini voice name</span>
          <Input
            value={settings.gemini_voice}
            onChange={(v) => setSettings({ ...settings, gemini_voice: v })}
            placeholder="Blank = match voice gender (Sulafat / Charon)"
          />
        </label>
      </Card>

      <Card>
        <CardTitle hint="Requires demo login credentials saved under Live demo → Product login.">
          Screenshare login
        </CardTitle>
        <Switch
          label="Show login during demo"
          description="On: run your recorded login/onboarding flow (or a live login if none). Off: silent auto sign-in — skip login/onboarding in the playlist."
          checked={includeLogin}
          disabled={savingLoginToggle || !loginUsername.trim()}
          onChange={(v) => void saveLoginToggle(v)}
        />
        {!loginUsername.trim() && (
          <p className="mt-2 text-[0.74rem] text-amber-600 dark:text-amber-400">
            Add demo username/password on the Live demo tab first.
          </p>
        )}
      </Card>

      <Card>
        <CardTitle
          hint="Optional BYOK — stored encrypted. Blank fields keep existing keys."
          right={
            <Button variant="secondary" onClick={saveKeys}>
              <Save size={14} /> Save keys
            </Button>
          }
        >
          Provider API keys
        </CardTitle>
        <div className="space-y-3">
          <label className="block space-y-1.5 text-[0.8rem]">
            <span className="font-medium text-[var(--muted)]">
              Gemini API key {settings.has_gemini_api_key ? "(saved)" : ""}
            </span>
            <Input
              type="password"
              value={geminiKey}
              onChange={setGeminiKey}
              placeholder={settings.has_gemini_api_key ? "••••••••  leave blank to keep" : "For Live audio + vision"}
            />
          </label>
          <label className="block space-y-1.5 text-[0.8rem]">
            <span className="font-medium text-[var(--muted)]">
              Groq API key {settings.has_groq_api_key ? "(saved)" : ""}
            </span>
            <Input
              type="password"
              value={groqKey}
              onChange={setGroqKey}
              placeholder={settings.has_groq_api_key ? "••••••••  leave blank to keep" : "For live phrasing / brain"}
            />
          </label>
        </div>
      </Card>
    </motion.div>
  );
}
