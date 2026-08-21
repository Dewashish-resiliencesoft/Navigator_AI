import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "motion/react";
import { ExternalLink, RefreshCw, Save } from "lucide-react";
import {
  api,
  type AgentSettings,
  type ProviderModel,
  type SpokenLanguage,
  type AgentGender,
} from "../lib/api";
import { useProductData } from "../lib/productData";
import { stagger } from "../lib/motion";
import { BarLoader, Button, Card, CardTitle, Field, Input, Switch } from "../components/ui";
import { errText, useUi } from "../store";

const LANG_LABELS: Record<SpokenLanguage, string> = {
  en: "English",
  hi: "Hindi",
};

type ProviderKind =
  | "gemini"
  | "groq"
  | "openai"
  | "anthropic"
  | "ollama"
  | "vllm"
  | "llamacpp"
  | "openrouter"
  | "huggingface";

const ALL_PROVIDERS: ProviderKind[] = [
  "gemini",
  "groq",
  "openai",
  "anthropic",
  "ollama",
  "vllm",
  "llamacpp",
  "openrouter",
  "huggingface",
];

const PROVIDER_META: Record<
  ProviderKind,
  { label: string; consoleUrl: string; hint: string; keyLabel: string }
> = {
  gemini: {
    label: "Google Gemini",
    consoleUrl: "https://aistudio.google.com/apikey",
    hint: "Sign in at Google AI Studio, create an API key named Navigator AI, then paste it below.",
    keyLabel: "Gemini API key",
  },
  groq: {
    label: "Groq",
    consoleUrl: "https://console.groq.com/keys",
    hint: "Sign in at Groq Console, create an API key named Navigator AI, then paste it below.",
    keyLabel: "Groq API key",
  },
  openai: {
    label: "OpenAI",
    consoleUrl: "https://platform.openai.com/api-keys",
    hint: "Sign in at OpenAI Platform, create an API key named Navigator AI, then paste it below.",
    keyLabel: "OpenAI API key",
  },
  anthropic: {
    label: "Anthropic",
    consoleUrl: "https://console.anthropic.com/settings/keys",
    hint: "Sign in at Anthropic Console, create an API key named Navigator AI, then paste it below.",
    keyLabel: "Anthropic API key",
  },
  ollama: {
    label: "Ollama (local)",
    consoleUrl: "",
    hint: "Enter Ollama base URL (default from server: http://localhost:11434).",
    keyLabel: "Ollama base URL",
  },
  vllm: {
    label: "vLLM (local)",
    consoleUrl: "",
    hint: "Enter vLLM OpenAI base URL (default: http://localhost:8000/v1).",
    keyLabel: "vLLM base URL",
  },
  llamacpp: {
    label: "llama.cpp (local)",
    consoleUrl: "",
    hint: "Enter llama.cpp OpenAI base URL (default: http://localhost:8000/v1).",
    keyLabel: "llama.cpp base URL",
  },
  openrouter: {
    label: "OpenRouter",
    consoleUrl: "https://openrouter.ai/keys",
    hint: "Create an OpenRouter key. Paste it below (API key).",
    keyLabel: "OpenRouter API key",
  },
  huggingface: {
    label: "Hugging Face Inference Providers",
    consoleUrl: "https://huggingface.co/settings/tokens",
    hint: "HF fine-grained token with Inference Providers access. Paste it below.",
    keyLabel: "Hugging Face token",
  },
};

type RoleKey = "brain" | "listening" | "speaking" | "hands";

const AGENT_ROLES: {
  role: RoleKey;
  providerField: keyof AgentSettings;
  modelField: keyof AgentSettings;
  label: string;
  hint: string;
  tag: string;
}[] = [
  {
    role: "brain",
    providerField: "role_brain_provider",
    modelField: "role_brain_model",
    label: "Brain",
    hint: "Main reasoning — decides what to say and what the demo should do next.",
    tag: "chat",
  },
  {
    role: "listening",
    providerField: "role_listening_provider",
    modelField: "role_listening_model",
    label: "Listening",
    hint: "Ears — transcribes visitor speech (Groq Whisper or Gemini Live audio in).",
    tag: "stt",
  },
  {
    role: "speaking",
    providerField: "role_speaking_provider",
    modelField: "role_speaking_model",
    label: "Speaking",
    hint: "Voice in the meeting — realtime audio out (Gemini Live or compatible).",
    tag: "live",
  },
  {
    role: "hands",
    providerField: "role_hands_provider",
    modelField: "role_hands_model",
    label: "Hands",
    hint: "Browser automation — picks clicks, typing, and scrolls after brain updates.",
    tag: "chat",
  },
];

function hasProviderKey(settings: AgentSettings, provider: ProviderKind): boolean {
  return (
    {
      gemini: settings.has_gemini_api_key,
      groq: settings.has_groq_api_key,
      openai: settings.has_openai_api_key,
      anthropic: settings.has_anthropic_api_key,
      openrouter: settings.has_openrouter_api_key,
      huggingface: settings.has_huggingface_api_key,
      ollama: (settings.ollama_base_url || "").trim().length > 0,
      vllm: (settings.vllm_base_url || "").trim().length > 0,
      llamacpp: (settings.llamacpp_base_url || "").trim().length > 0,
    }[provider] ?? false
  );
}

function connectedProviders(settings: AgentSettings): ProviderKind[] {
  return ALL_PROVIDERS.filter((p) => hasProviderKey(settings, p));
}

type ModelFieldKey =
  | "live_conversational_model"
  | "brain_reasoning_model"
  | "brain_planning_model"
  | "brain_phrasing_model"
  | "brain_classify_model"
  | "brain_stt_model"
  | "brain_vision_text_model"
  | "brain_vision_image_model";

const MODEL_FIELDS: {
  key: ModelFieldKey;
  label: string;
  provider: ProviderKind;
  tag: string;
}[] = [
  { key: "live_conversational_model", label: "Live voice model", provider: "gemini", tag: "live" },
  { key: "brain_reasoning_model", label: "Flash reasoning", provider: "gemini", tag: "chat" },
  { key: "brain_vision_text_model", label: "Vision text", provider: "gemini", tag: "chat" },
  { key: "brain_vision_image_model", label: "Vision image", provider: "gemini", tag: "vision" },
  { key: "brain_planning_model", label: "Planning", provider: "groq", tag: "chat" },
  { key: "brain_phrasing_model", label: "Phrasing", provider: "groq", tag: "chat" },
  { key: "brain_classify_model", label: "Classifier", provider: "groq", tag: "chat" },
  { key: "brain_stt_model", label: "Speech-to-text", provider: "groq", tag: "stt" },
];

const CUSTOM_OPTION = "__custom__";

function modelsForTag(models: ProviderModel[], tag: string): ProviderModel[] {
  const tagged = models.filter((m) => m.tags.includes(tag));
  return tagged.length ? tagged : models;
}

function RoleModelSelect({
  role,
  settings,
  providerModels,
  onChange,
}: {
  role: (typeof AGENT_ROLES)[number];
  settings: AgentSettings;
  providerModels: Record<ProviderKind, ProviderModel[]>;
  onChange: (patch: Partial<AgentSettings>) => void;
}) {
  const provider = (settings[role.providerField] as string) || "";
  const model = (settings[role.modelField] as string) || "";
  const connected = connectedProviders(settings);
  const models = provider
    ? modelsForTag(providerModels[provider as ProviderKind] || [], role.tag)
    : [];
  const inList = !model || models.some((m) => m.id === model);
  const selectValue = inList ? model : CUSTOM_OPTION;

  return (
    <div
      className="space-y-3 rounded-xl border p-4"
      style={{ borderColor: "var(--line)" }}
    >
      <div>
        <div className="text-[0.9rem] font-medium">{role.label}</div>
        <p className="text-[0.76rem] text-[var(--muted)]">{role.hint}</p>
      </div>
      <label className="block space-y-1.5 text-[0.8rem]">
        <span className="font-medium text-[var(--muted)]">Provider</span>
        <select
          className="w-full rounded-lg border bg-transparent px-3 py-2 text-[0.85rem]"
          style={{ borderColor: "var(--line)" }}
          value={provider}
          onChange={(e) =>
            onChange({
              [role.providerField]: e.target.value,
              [role.modelField]: "",
            })
          }
        >
          <option value="">Server default</option>
          {connected.map((p) => (
            <option key={p} value={p}>
              {PROVIDER_META[p].label}
            </option>
          ))}
        </select>
      </label>
      {provider && (
        <label className="block space-y-1.5 text-[0.8rem]">
          <span className="font-medium text-[var(--muted)]">Model</span>
          <select
            className="w-full rounded-lg border bg-transparent px-3 py-2 text-[0.85rem]"
            style={{ borderColor: "var(--line)" }}
            value={selectValue}
            onChange={(e) => {
              const v = e.target.value;
              if (v === CUSTOM_OPTION) {
                onChange({ [role.modelField]: model && !inList ? model : "" });
                return;
              }
              onChange({ [role.modelField]: v });
            }}
          >
            <option value="">Use provider default</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label === m.id ? m.id : `${m.label} (${m.id})`}
              </option>
            ))}
            <option value={CUSTOM_OPTION}>Custom model id…</option>
          </select>
          {selectValue === CUSTOM_OPTION && (
            <Input
              value={model}
              onChange={(v) => onChange({ [role.modelField]: v })}
              placeholder="Model id not in list"
            />
          )}
        </label>
      )}
      {provider && !hasProviderKey(settings, provider as ProviderKind) && (
        <p className="text-[0.74rem] text-amber-600 dark:text-amber-400">
          Connect {PROVIDER_META[provider as ProviderKind]?.label} below first.
        </p>
      )}
    </div>
  );
}

function ModelFieldSelect({
  field,
  value,
  models,
  onChange,
}: {
  field: (typeof MODEL_FIELDS)[number];
  value: string;
  models: ProviderModel[];
  onChange: (next: string) => void;
}) {
  const options = useMemo(() => modelsForTag(models, field.tag), [models, field.tag]);
  const inList = !value || options.some((m) => m.id === value);
  const selectValue = inList ? value : CUSTOM_OPTION;

  return (
    <label className="block space-y-1.5 text-[0.8rem]">
      <span className="font-medium text-[var(--muted)]">{field.label}</span>
      <select
        className="w-full rounded-lg border bg-transparent px-3 py-2 text-[0.85rem]"
        style={{ borderColor: "var(--line)" }}
        value={selectValue}
        onChange={(e) => {
          const v = e.target.value;
          if (v === CUSTOM_OPTION) {
            onChange(value && !inList ? value : "");
            return;
          }
          onChange(v);
        }}
      >
        <option value="">Use server default</option>
        {options.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label === m.id ? m.id : `${m.label} (${m.id})`}
          </option>
        ))}
        <option value={CUSTOM_OPTION}>Custom model id…</option>
      </select>
      {selectValue === CUSTOM_OPTION && (
        <Input
          value={value}
          onChange={onChange}
          placeholder="e.g. gemini-3.6-flash or llama-3.3-70b-versatile"
        />
      )}
    </label>
  );
}

function ProviderConnectCard({
  settings,
  keyDrafts,
  onKeyDraft,
  onSaveKey,
  onFetchModels,
  fetchingModels,
  savingKeys,
  forceProvider,
}: {
  settings: AgentSettings;
  keyDrafts: Record<ProviderKind, string>;
  onKeyDraft: (provider: ProviderKind, v: string) => void;
  onSaveKey: (provider: ProviderKind) => void;
  onFetchModels: (provider: ProviderKind, previewKey?: string) => void;
  fetchingModels: Record<ProviderKind, boolean>;
  savingKeys: Record<ProviderKind, boolean>;
  forceProvider?: ProviderKind;
}) {
  const [selected, setSelected] = useState<ProviderKind>("gemini");
  const meta = PROVIDER_META[selected];
  const hasKey = hasProviderKey(settings, selected);
  const draft = keyDrafts[selected];
  const saving = savingKeys[selected];
  const fetching = fetchingModels[selected];
  const connected = ALL_PROVIDERS.filter((p) => hasProviderKey(settings, p));

  useEffect(() => {
    if (forceProvider) setSelected(forceProvider);
  }, [forceProvider]);

  return (
    <div className="space-y-4 rounded-xl border p-4" style={{ borderColor: "var(--line)" }}>
      {/* Provider selector */}
      <label className="block space-y-1.5 text-[0.8rem]">
        <span className="font-medium text-[var(--muted)]">Provider</span>
        <select
          className="w-full rounded-lg border bg-transparent px-3 py-2 text-[0.85rem]"
          style={{ borderColor: "var(--line)" }}
          value={selected}
          onChange={(e) => setSelected(e.target.value as ProviderKind)}
        >
          {ALL_PROVIDERS.map((p) => (
            <option key={p} value={p}>
              {PROVIDER_META[p].label}{hasProviderKey(settings, p) ? " ✓" : ""}
            </option>
          ))}
        </select>
      </label>

      {/* Hint + console link */}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="flex-1 text-[0.76rem] text-[var(--muted)]">{meta.hint}</p>
        {meta.consoleUrl ? (
          <Button
            variant="secondary"
            onClick={() =>
              window.open(meta.consoleUrl, "_blank", "noopener,noreferrer")
            }
          >
            <ExternalLink size={13} /> Get key
          </Button>
        ) : null}
      </div>

      {/* Key input */}
      <label className="block space-y-1.5 text-[0.8rem]">
        <span className="font-medium text-[var(--muted)]">
          {meta.keyLabel}{hasKey ? " (saved — paste to replace)" : ""}
        </span>
        <input
          type={selected === "ollama" || selected === "vllm" || selected === "llamacpp" ? "text" : "password"}
          value={draft}
          onChange={(e) => onKeyDraft(selected, e.target.value)}
          onBlur={() => { if (draft.trim()) onSaveKey(selected); }}
          placeholder={
            selected === "ollama" || selected === "vllm" || selected === "llamacpp"
              ? hasKey
                ? "Paste to replace saved base URL"
                : "Paste base URL (e.g. http://localhost:11434)"
              : hasKey
                ? "Paste to replace saved key"
                : "Paste API key here"
          }
          className="w-full rounded-lg border bg-transparent px-3 py-2 text-[0.85rem] outline-none focus:ring-1 focus:ring-[var(--accent)]"
          style={{ borderColor: "var(--line)" }}
        />
      </label>

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          disabled={saving || !draft.trim()}
          onClick={() => onSaveKey(selected)}
        >
          <Save size={14} />{saving ? "Saving…" : "Save key"}
        </Button>
        <Button
          variant="ghost"
          disabled={fetching || (!hasKey && !draft.trim())}
          onClick={() => onFetchModels(selected, draft.trim() || undefined)}
        >
          <RefreshCw size={14} className={fetching ? "animate-spin" : ""} />
          {draft.trim() && !hasKey ? "Verify & load" : "Refresh models"}
        </Button>
      </div>

      {/* Connected chips */}
      {connected.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          <span className="text-[0.74rem] text-[var(--muted)]">Connected:</span>
          {connected.map((p) => (
            <span
              key={p}
              className="rounded-full bg-green-100 px-2.5 py-0.5 text-[0.72rem] font-medium text-green-800 dark:bg-green-900/30 dark:text-green-300"
            >
              {PROVIDER_META[p].label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function Settings() {
  const { ok, err } = useUi();
  const coachTarget = useUi((s) => s.coach?.target);
  const epoch = useProductData((s) => s.epoch);
  const [loading, setLoading] = useState(true);
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [savingPersona, setSavingPersona] = useState(false);
  const [keyDrafts, setKeyDrafts] = useState<Record<ProviderKind, string>>({
    gemini: "",
    groq: "",
    openai: "",
    anthropic: "",
    ollama: "",
    vllm: "",
    llamacpp: "",
    openrouter: "",
    huggingface: "",
  });
  const [providerModels, setProviderModels] = useState<Record<ProviderKind, ProviderModel[]>>({
    gemini: [],
    groq: [],
    openai: [],
    anthropic: [],
    ollama: [],
    vllm: [],
    llamacpp: [],
    openrouter: [],
    huggingface: [],
  });
  const [fetchingModels, setFetchingModels] = useState<Record<ProviderKind, boolean>>({
    gemini: false,
    groq: false,
    openai: false,
    anthropic: false,
    ollama: false,
    vllm: false,
    llamacpp: false,
    openrouter: false,
    huggingface: false,
  });
  const [savingKeys, setSavingKeys] = useState<Record<ProviderKind, boolean>>({
    gemini: false,
    groq: false,
    openai: false,
    anthropic: false,
    ollama: false,
    vllm: false,
    llamacpp: false,
    openrouter: false,
    huggingface: false,
  });
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [includeLogin, setIncludeLogin] = useState(false);
  const [loginUsername, setLoginUsername] = useState("");
  const [loginUrl, setLoginUrl] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [hasPassword, setHasPassword] = useState(false);
  const [changingPass, setChangingPass] = useState(false);
  const [savingLogin, setSavingLogin] = useState(false);

  const fetchProviderModels = useCallback(
    async (provider: ProviderKind, previewKey?: string) => {
      setFetchingModels((prev) => ({ ...prev, [provider]: true }));
      try {
        const data = previewKey
          ? await (provider === "ollama" || provider === "vllm" || provider === "llamacpp"
              ? api.previewAgentProviderModels({ provider, base_url: previewKey })
              : api.previewAgentProviderModels({ provider, api_key: previewKey }))
          : await api.getAgentProviderModels(provider);
        setProviderModels((prev) => ({ ...prev, [provider]: data.models }));
        ok(`${PROVIDER_META[provider].label} models loaded (${data.models.length}).`);
      } catch (e) {
        err(errText(e));
      } finally {
        setFetchingModels((prev) => ({ ...prev, [provider]: false }));
      }
    },
    [ok, err],
  );

  const saveModelOverrides = async () => {
    if (!settings) return;
    try {
      await api.putAgentSettings({
        role_brain_provider: settings.role_brain_provider,
        role_brain_model: settings.role_brain_model,
        role_listening_provider: settings.role_listening_provider,
        role_listening_model: settings.role_listening_model,
        role_speaking_provider: settings.role_speaking_provider,
        role_speaking_model: settings.role_speaking_model,
        role_hands_provider: settings.role_hands_provider,
        role_hands_model: settings.role_hands_model,
        live_conversational_model: settings.live_conversational_model,
        brain_reasoning_model: settings.brain_reasoning_model,
        brain_planning_model: settings.brain_planning_model,
        brain_phrasing_model: settings.brain_phrasing_model,
        brain_classify_model: settings.brain_classify_model,
        brain_stt_model: settings.brain_stt_model,
        brain_vision_text_model: settings.brain_vision_text_model,
        brain_vision_image_model: settings.brain_vision_image_model,
      });
      ok("Agent models saved.");
    } catch (e) {
      err(errText(e));
    }
  };

  const saveProviderKey = async (provider: ProviderKind) => {
    const draft = keyDrafts[provider];
    if (!draft.trim()) {
      err(
        provider === "ollama" || provider === "vllm" || provider === "llamacpp"
          ? "Paste a base URL first."
          : "Paste an API key first.",
      );
      return;
    }
    if (!settings) {
      err("Load settings first.");
      return;
    }
    setSavingKeys((prev) => ({ ...prev, [provider]: true }));
    try {
      if (provider === "ollama") {
        const d = await api.putAgentSettings({ ollama_base_url: draft.trim() });
        setSettings((prev) => (prev ? { ...prev, ...d } : prev));
      } else if (provider === "vllm") {
        const d = await api.putAgentSettings({ vllm_base_url: draft.trim() });
        setSettings((prev) => (prev ? { ...prev, ...d } : prev));
      } else if (provider === "llamacpp") {
        const d = await api.putAgentSettings({
          llamacpp_base_url: draft.trim(),
        });
        setSettings((prev) => (prev ? { ...prev, ...d } : prev));
      } else {
        const body: {
          gemini_api_key?: string;
          groq_api_key?: string;
          openai_api_key?: string;
          anthropic_api_key?: string;
          openrouter_api_key?: string;
          huggingface_api_key?: string;
        } = {};
        if (provider === "gemini") body.gemini_api_key = draft.trim();
        if (provider === "groq") body.groq_api_key = draft.trim();
        if (provider === "openai") body.openai_api_key = draft.trim();
        if (provider === "anthropic") body.anthropic_api_key = draft.trim();
        if (provider === "openrouter") body.openrouter_api_key = draft.trim();
        if (provider === "huggingface") body.huggingface_api_key = draft.trim();
        const d = await api.putAgentProviderKeys(body);
        setSettings((prev) => (prev ? { ...prev, ...d } : prev));
      }
      setKeyDrafts((prev) => ({ ...prev, [provider]: "" }));
      ok(
        provider === "ollama" || provider === "vllm" || provider === "llamacpp"
          ? `${PROVIDER_META[provider].label} base URL saved.`
          : `${PROVIDER_META[provider].label} key saved.`,
      );
      await fetchProviderModels(provider);
    } catch (e) {
      err(errText(e));
    } finally {
      setSavingKeys((prev) => ({ ...prev, [provider]: false }));
    }
  };

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
      setHasPassword(!!login.has_password);
      setChangingPass(!login.has_password);
      setLoginPass("");
      setKeyDrafts({
        gemini: "",
        groq: "",
        openai: "",
        anthropic: "",
        ollama: "",
        vllm: "",
        llamacpp: "",
        openrouter: "",
        huggingface: "",
      });
      for (const p of ALL_PROVIDERS) {
        if (hasProviderKey(d, p)) void fetchProviderModels(p);
      }
    } catch (e) {
      err(errText(e));
    } finally {
      setLoading(false);
    }
  }, [err, fetchProviderModels]);

  useEffect(() => {
    void load();
  }, [load, epoch]);

  // Skip auto-save on initial load per product epoch.
  const loadedFromApiRef = useRef(false);
  useEffect(() => {
    loadedFromApiRef.current = false;
  }, [epoch]);

  const toggleExtraLang = (lang: SpokenLanguage) => {
    if (!settings) return;
    const has = settings.extra_languages.includes(lang);
    const next = has
      ? settings.extra_languages.filter((l) => l !== lang)
      : [...settings.extra_languages, lang];
    setSettings({ ...settings, extra_languages: next.length ? next : [lang] });
  };

  const saveSettings = async () => {
    if (!settings || savingPersona) return;
    try {
      setSavingPersona(true);
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
    } finally {
      setSavingPersona(false);
    }
  };

  // Make persona options dynamic: persist on change (debounced).
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!settings || loading) return;
    if (!loadedFromApiRef.current) {
      loadedFromApiRef.current = true;
      return;
    }
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => {
      void saveSettings();
    }, 700);
    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    settings?.default_language,
    settings?.extra_languages,
    settings?.agent_gender,
    settings?.agent_name,
    settings?.tone,
    settings?.gemini_voice,
    loading,
  ]);

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
        username: loginUsername.trim(),
        include_login_in_default_flow: includeLogin,
      };
      if (changingPass) body.password = loginPass;
      else body.password = null;
      const saved = await api.putProductLogin(body);
      setLoginUrl(saved.login_url || "");
      setLoginUsername(saved.username || "");
      setHasPassword(!!saved.has_password);
      setIncludeLogin(!!saved.include_login_in_default_flow);
      setChangingPass(!saved.has_password);
      setLoginPass("");
      ok("Product login saved.");
    } catch (e) {
      err(errText(e));
    } finally {
      setSavingLogin(false);
    }
  };

  const saveLoginToggle = async (enabled: boolean) => {
    if (!loginUsername.trim()) {
      err("Save username first.");
      return;
    }
    setSavingLogin(true);
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
          ? "Demo will show login / onboarding on screenshare."
          : "Demo uses silent auto sign-in.",
      );
    } catch (e) {
      err(errText(e));
    } finally {
      setSavingLogin(false);
    }
  };

  if (loading || !settings) {
    return <BarLoader label="Loading agent settings…" />;
  }

  return (
    <motion.div className="space-y-5" variants={stagger()} initial="hidden" animate="show">
      <Card>
        <CardTitle
          hint="Voice gender, name, tone, languages, and Gemini Live voice — keep them consistent (Hindi verb forms follow gender)."
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
          <label className="col-span-full block space-y-1.5 text-[0.8rem]">
            <span className="font-medium text-[var(--muted)]">Gemini voice name</span>
            <Input
              value={settings.gemini_voice}
              onChange={(v) => setSettings({ ...settings, gemini_voice: v })}
              placeholder="Blank = match voice gender (Sulafat / Charon)"
            />
            <span className="text-[0.72rem] text-[var(--muted)]">
              Meet voice is Gemini Live. Override if needed (Sulafat, Charon).
            </span>
          </label>
        </div>
      </Card>

      <Card>
        <CardTitle
          hint="Connect frontier providers (Gemini, Groq, OpenAI, Anthropic), then assign Brain, Listening, Speaking, and Hands models."
          right={
            <Button variant="secondary" onClick={() => void saveModelOverrides()}>
              Save models
            </Button>
          }
        >
          Direct agent connection
        </CardTitle>

        <div className="space-y-5">
          <div className="text-[0.82rem] text-[var(--muted)]">
            Defaults: <strong>Brain</strong> Gemini Flash · <strong>Listening/Speaking</strong>{" "}
            Gemini Live audio · <strong>Hands</strong> Groq. Override per role below.
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            {AGENT_ROLES.map((role) => (
              <RoleModelSelect
                key={role.role}
                role={role}
                settings={settings}
                providerModels={providerModels}
                onChange={(patch) => setSettings({ ...settings, ...patch })}
              />
            ))}
          </div>

          <div className="space-y-3" data-coach="settings-gemini">
            <div className="text-[0.85rem] font-medium">Provider API keys</div>
            <ProviderConnectCard
              settings={settings}
              keyDrafts={keyDrafts}
              onKeyDraft={(p, v) => setKeyDrafts((prev) => ({ ...prev, [p]: v }))}
              onSaveKey={(p) => void saveProviderKey(p)}
              onFetchModels={(p, preview) => void fetchProviderModels(p, preview)}
              fetchingModels={fetchingModels}
              savingKeys={savingKeys}
              forceProvider={
                coachTarget === "settings-gemini" ? "gemini" : undefined
              }
            />
          </div>

          <details
            open={showAdvanced}
            onToggle={(e) => setShowAdvanced((e.target as HTMLDetailsElement).open)}
          >
            <summary className="cursor-pointer text-[0.82rem] font-medium text-[var(--muted)]">
              Advanced per-node overrides
            </summary>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              {MODEL_FIELDS.map((field) => {
                const models = providerModels[field.provider] || [];
                return (
                  <ModelFieldSelect
                    key={field.key}
                    field={field}
                    value={settings[field.key]}
                    models={models}
                    onChange={(v) => setSettings({ ...settings, [field.key]: v })}
                  />
                );
              })}
            </div>
          </details>
        </div>
      </Card>

      <Card>
        <CardTitle hint="Playwright sign-in for demos and Product Explore. Credentials stay out of the site graph.">
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
              value={loginUsername}
              onChange={setLoginUsername}
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
            disabled={savingLogin || !loginUsername.trim()}
            onChange={(v) => void saveLoginToggle(v)}
          />
        </div>

        <Button
          onClick={() => void saveLogin()}
          disabled={
            savingLogin ||
            !loginUsername.trim() ||
            (changingPass && !loginPass && !hasPassword)
          }
        >
          {savingLogin ? "Saving…" : "Save product login"}
        </Button>
      </Card>
    </motion.div>
  );
}
