export type DemoStatus = "starting" | "running" | "finished" | "failed";

export type UserPreferences = {
  hide_get_started_card: boolean;
  onboarding_wizard_dismissed: boolean;
  onboarding_wizard_completed: boolean;
};

export type DemoOrigin = "dashboard_test" | "public_embed";

export type AutonomyMode = "guided" | "adaptive" | "explorer";

export type SpokenLanguage = "en" | "hi";
export type AgentGender = "female" | "male";

export type AgentSettings = {
  default_language: SpokenLanguage;
  extra_languages: SpokenLanguage[];
  agent_gender: AgentGender;
  agent_name: string;
  tone: string;
  gemini_voice: string;
  has_gemini_api_key: boolean;
  has_groq_api_key: boolean;
  updated_at: string | null;
};

export type ReadinessCheck = {
  id: string;
  ok: boolean;
  message: string;
  blocking: boolean;
};

export type DemoReadiness = {
  score: number;
  autonomy_mode: AutonomyMode;
  checks: ReadinessCheck[];
};

export type PublishChecklist = {
  readiness: DemoReadiness;
  eval_score_pct: number | null;
  autonomy_recommendation: string;
};

export type DecisionTrace = {
  id: string;
  session_id: string;
  utterance: string;
  branch: string;
  chosen_flow_id: string | null;
  spoken: string;
  flow_candidates: (string | number)[][];
  knowledge_hits: (string | number)[][];
  detail: string;
  created_at: string;
};

export type Demo = {
  demo_id: string;
  product_id: string;
  revision: number;
  session_id: string;
  origin: DemoOrigin;
  status: DemoStatus;
  page_id: string;
  actions: number;
  failures: number;
  error: string | null;
  said: string[];
  meeting_url: string | null;
  platform: string | null;
  bot_in_meeting: boolean;
};

export type BioField = { key: string; label: string; value: string };
export type SystemMetrics = {
  host_label: string;
  uptime_s: number;
  cpu_percent: number;
  cpu_count: number;
  memory_percent: number;
  memory_used_mb: number;
  memory_total_mb: number;
  net_sent_bytes: number;
  net_recv_bytes: number;
  gpu: {
    active: boolean;
    name: string;
    utilization_percent: number | null;
    memory_used_mb: number | null;
    memory_total_mb: number | null;
  };
  services: { name: string; status: string; detail: string }[];
  processes: { name: string; status: string; cpu: string; mem: string }[];
  health: { name: string; ok: boolean; detail?: string }[];
  token_usage?: {
    days: number;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    calls: number;
    has_usage: boolean;
    uses_byok: boolean;
    billing_label: string;
    byok: {
      has_groq_api_key: boolean;
      has_gemini_api_key: boolean;
      updated_at: string | null;
    };
    platform: { input_tokens: number; output_tokens: number; total_tokens: number; calls: number };
    client: { input_tokens: number; output_tokens: number; total_tokens: number; calls: number };
    providers: {
      provider: string;
      billed_to: string;
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
      calls: number;
    }[];
    client_models: {
      model: string;
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
      calls: number;
    }[];
    typical_platform_per_demo: {
      input_tokens: number;
      output_tokens: number;
      calls: number;
    };
  } | null;
};
export type DemoScriptBeat = {
  id: string;
  kind: string;
  spoken?: string;
  spoken_source?: string;
  asks_visitor?: boolean;
  on_screen?: string;
  flow_id?: string;
  page_id?: string;
  step_index?: number;
  flow_title?: string;
  phase?: string;
  field?: string;
  field_alias?: string;
  live_question?: string;
  example_value?: string;
  knowledge_refs?: string[];
  uses_intake_tokens?: boolean;
  speak_ms?: number;
  /** Ms into the flow when this narration starts during playback. */
  speak_at_ms?: number;
  /** Ms into the flow when this step's action fires. */
  act_at_ms?: number;
  needs_approval?: boolean;
  approval_reason?: string;
};

export type DemoScriptResponse = {
  revision: number;
  published_revision: number | null;
  playlist: Flow[];
  beats: DemoScriptBeat[];
  context?: string;
  sources_used?: string[];
  /** Recorded length of each flow in ms, keyed by flow_id. */
  flow_total_ms?: Record<string, number>;
  stats?: {
    beat_count: number;
    asks_visitor_count: number;
    spoken_count: number;
  };
};

export type Flow = {
  name: string;
  page_id: string;
  flow_id: string;
  order?: number;
  purpose?: string;
  tags?: string[];
  auto_name?: string;
  verdict?: "ready" | "needs_review" | "broken" | string;
  risk_score?: number;
  pass_rate?: number;
};

export type RecorderStatus = {
  active?: boolean;
  recording?: boolean;
  status?: string;
  steps?: number;
  flow_name?: string | null;
  error?: string | null;
  phase?: "setup" | "capturing" | "done" | string;
  setup_discarded?: number;
  flagged?: Array<{ tool?: string; selector?: string; reason?: string }>;
  narrate?: boolean;
  narration_chunks?: number;
  save_mode?: "new" | "update" | string;
};

export type ExploreQuestion = {
  qid: string;
  alias: string;
  prompt: string;
  context: Record<string, string>;
};

export type ExploreFlagged = {
  label: string;
  selector: string;
  url: string;
  reason: string;
  source: string;
  element_key?: string;
};

export type ExploreFieldDecision = {
  alias: string;
  label: string;
  classification: "guessable_safe" | "business_specific" | string;
  value: string;
  answered_by: "auto" | "client" | "skipped_timeout" | "skipped_client" | string;
};

export type ExploreStatus = {
  active: boolean;
  has_credentials?: boolean;
  job_id?: string;
  phase?: string;
  visited?: number;
  visited_paths?: string[];
  steps?: number;
  flagged?: ExploreFlagged[];
  field_decisions?: ExploreFieldDecision[];
  recent_events?: ExploreEvent[];
  elapsed_s?: number;
  progress_pct?: number;
  actions_taken?: number;
  save_mode?: string;
  target_flow_id?: string | null;
  target_flow_name?: string | null;
  new_flow_name?: string | null;
  focus_hint?: string | null;
  budget?: {
    max_pages: number;
    max_steps: number;
    max_wall_clock_s: number;
  };
  error?: string;
  flow_id?: string;
  revision?: number | null;
  stop_reason?: string;
  pending_question?: ExploreQuestion | null;
};

/** One frame off the exploration WebSocket. `type` discriminates the payload. */
export type ExploreEvent = {
  type: string;
  msg?: string;
  level?: string;
  phase?: string;
  qid?: string;
  alias?: string;
  prompt?: string;
  context?: Record<string, string>;
  [key: string]: unknown;
};

export type MetricPoint = {
  day: string;
  actions: number;
  sessions: number;
  failures: number;
};

export type Metrics = {
  /** Rolling window length (days) for every counter below. */
  days?: number;
  /** Test demos the Client ran from this dashboard in the window. */
  test_sessions: number;
  /** Demo runs started in the window (test + live). Matches Logs + Sessions chart. */
  actions: number;
  sessions: number;
  /** Failed tool / verification steps in the window. Sum matches run fail_count column. */
  failures: number;
  /** Demo runs whose status is ``failed`` (crash / join error). */
  failed_runs?: number;
  /** Demo runs with at least one failed tool step. */
  runs_with_step_failures?: number;
  verified: number;
  passed: number;
  last_seen: string | null;
  series: MetricPoint[];
  /** Same as ``series`` — kept for older clients. */
  run_series?: MetricPoint[];
  demos?: { total: number; running: number; failed: number };
  live: { total: number; running: number; failed: number };
  test?: { total: number; running: number; failed: number };
  /** Billable End User traffic only (excludes dashboard test demos). */
  visitor?: { sessions: number; actions: number; failures: number };
};

/** Shared metrics + runs window for Overview and Logs. */
export const DASHBOARD_DAYS = 14;

export type DemoRun = {
  session_id: string;
  demo_id: string;
  product_id: string;
  platform: string;
  status: string;
  origin: DemoOrigin;
  host_os: string;
  host_release: string;
  host_machine: string;
  host_name: string;
  browser: string;
  meeting_label: string;
  started_at: string;
  ended_at: string | null;
  fail_count: number;
};

export type RunEvent = {
  call_id: string;
  session_id: string;
  page: string;
  timestamp: string;
  tool_call: { tool: string; selector?: string; [k: string]: unknown };
  actual_result: { ok: boolean; detail: string; tool: string };
  verify: { passed: boolean; actual: string } | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const TOKEN_KEY = "nav_access_token";

let _accessToken: string | null =
  typeof sessionStorage !== "undefined"
    ? sessionStorage.getItem(TOKEN_KEY)
    : null;
let _refreshPromise: Promise<void> | null = null;

export function setAccessToken(token: string | null) {
  _accessToken = token;
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode */
  }
}

export function getAccessToken() {
  return _accessToken;
}

async function doRefresh() {
  const res = await fetch("/v1/auth/refresh", {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    setAccessToken(null);
    throw new ApiError("Session expired", 401);
  }
  const data = await res.json();
  setAccessToken(data.access_token);
}

const AUTH_PUBLIC = new Set([
  "/v1/auth/login",
  "/v1/auth/signup",
  "/v1/auth/refresh",
  "/v1/auth/logout",
]);

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!_accessToken && !AUTH_PUBLIC.has(path)) {
    if (!_refreshPromise) {
      _refreshPromise = doRefresh().finally(() => {
        _refreshPromise = null;
      });
    }
    await _refreshPromise;
  }

  const doRequest = async () => {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...((init?.headers as Record<string, string>) ?? {}),
    };
    if (_accessToken) {
      headers["Authorization"] = `Bearer ${_accessToken}`;
    }
    const res = await fetch(path, { ...init, headers, credentials: "include" });

    if (res.status === 401 && !AUTH_PUBLIC.has(path)) {
      throw new ApiError("unauthorized", 401);
    }

    const text = await res.text();
    let body: any = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = { detail: text };
    }

    if (!res.ok) {
      const detail = body?.detail ?? body?.message ?? text ?? res.statusText;
      throw new ApiError(
        typeof detail === "string" ? detail : JSON.stringify(detail),
        res.status,
      );
    }
    return body as T;
  };

  try {
    return await doRequest();
  } catch (err) {
    if (
      err instanceof ApiError &&
      err.status === 401 &&
      !AUTH_PUBLIC.has(path)
    ) {
      if (!_refreshPromise) {
        _refreshPromise = doRefresh().finally(() => {
          _refreshPromise = null;
        });
      }
      await _refreshPromise;
      return await doRequest();
    }
    throw err;
  }
}

export async function login(email: string, password: string) {
  const data = await request<{ access_token: string }>("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setAccessToken(data.access_token);
}

export async function signup(
  company_name: string,
  email: string,
  password: string,
) {
  const data = await request<{ access_token: string }>("/v1/auth/signup", {
    method: "POST",
    body: JSON.stringify({ company_name, email, password }),
  });
  setAccessToken(data.access_token);
}

export async function logout() {
  try {
    await request("/v1/auth/logout", { method: "POST" });
  } catch {
    /* ignore */
  }
  setAccessToken(null);
}

const get = <T>(path: string) => request<T>(path);
const send = <T>(path: string, method: string, body?: unknown) =>
  request<T>(path, { method, body: JSON.stringify(body ?? {}) });


export type StartDemoBody = {
  platform: string;
  topic?: string;
  auto_play?: boolean;
  intake: {
    name: string;
    company: string;
    business_type: string;
    looking_for: string;
  };
};

export const api = {
  login,
  signup,
  logout,
  checkAuth: async () => {
    if (_accessToken) return true;
    try {
      await doRefresh();
      return !!_accessToken;
    } catch {
      return false;
    }
  },

  getUserPreferences: () => get<UserPreferences>("/client/api/user/preferences"),
  putUserPreferences: (patch: Partial<UserPreferences>) =>
    send<UserPreferences>("/client/api/user/preferences", "PUT", patch),
  bootstrap: () =>
    request<{ ok: boolean; product_id: string; api_key: string | null; message: string }>(
      "/client/api/bootstrap",
      { method: "POST", body: "{}" },
    ),

  listDemos: () => get<Demo[]>("/client/api/demos"),
  getDemo: (id: string) => get<Demo>(`/client/api/demos/${id}`),
  startDemo: async (body: StartDemoBody) => {
    // LiveDemoView nests MeetingOut under `meeting`; top-level meeting_url
    // also comes from the handle. Prefer either so the UI never blanks.
    const d = await send<Demo & { meeting?: { url?: string } }>(
      "/client/api/demos/start",
      "POST",
      body,
    );
    if (!d.meeting_url && d.meeting?.url) {
      d.meeting_url = d.meeting.url;
    }
    return d;
  },
  endDemo: (id: string) => send<Demo>(`/client/api/demos/${id}/end`, "POST"),

  metrics: (days = DASHBOARD_DAYS) => get<Metrics>(`/client/api/metrics?days=${days}`),
  getSystemMetrics: () => get<SystemMetrics>("/client/api/system/health"),

  listRuns: (days = DASHBOARD_DAYS) => get<DemoRun[]>(`/client/api/runs?days=${days}`),
  getRun: (sessionId: string) => get<DemoRun>(`/client/api/runs/${sessionId}`),
  runEvents: (sessionId: string) =>
    get<RunEvent[]>(`/client/api/runs/${sessionId}/events`),

  getBio: () => get<{ fields: BioField[] }>("/client/api/bio"),
  putBio: (fields: BioField[]) => send<unknown>("/client/api/bio", "PUT", { fields }),

  getKnowledge: () => get<{ markdown: string }>("/client/api/knowledge"),
  putKnowledge: (markdown: string) =>
    send<unknown>("/client/api/knowledge", "PUT", { markdown }),

  getProductDomain: () => get<{ base_url: string; placeholder: boolean }>("/client/api/product-domain"),
  putProductDomain: (base_url: string) => send<{ ok: boolean; base_url: string; revision: number; placeholder: boolean }>("/client/api/product-domain", "PUT", { base_url }),
  getTier2: () => get<{ enabled: boolean }>("/client/api/tier2"),
  putTier2: (enabled: boolean) =>
    send<{ ok: boolean; enabled: boolean }>("/client/api/tier2", "PUT", { enabled }),

  getAutonomyMode: () =>
    get<{ mode: AutonomyMode; tier2_enabled: boolean; handoff_webhook_url: string }>(
      "/client/api/autonomy-mode",
    ),
  putAutonomyMode: (mode: AutonomyMode) =>
    send<{ ok: boolean; mode: AutonomyMode; tier2_enabled: boolean }>(
      "/client/api/autonomy-mode",
      "PUT",
      { mode },
    ),

  getDemoReadiness: (origin: DemoOrigin = "dashboard_test") =>
    get<DemoReadiness>(`/client/api/demo-readiness?origin=${origin}`),

  getPublishChecklist: () => get<PublishChecklist>("/client/api/publish-checklist"),

  runDecisions: (sessionId: string) =>
    get<DecisionTrace[]>(`/client/api/runs/${sessionId}/decisions`),

  getProductLogin: () =>
    get<{
      login_url: string;
      username: string;
      has_password: boolean;
      include_login_in_default_flow: boolean;
      updated_at: string | null;
    }>("/client/api/product-login"),
  putProductLogin: (body: {
    login_url: string;
    username: string;
    password?: string | null;
    include_login_in_default_flow: boolean;
  }) =>
    send<{
      ok: boolean;
      login_url: string;
      username: string;
      has_password: boolean;
      include_login_in_default_flow: boolean;
      updated_at: string | null;
    }>("/client/api/product-login", "PUT", body),
  deleteProductLogin: () =>
    send<{ ok: boolean }>("/client/api/product-login", "DELETE"),

  getAgentSettings: () => get<AgentSettings>("/client/api/agent-settings"),
  putAgentSettings: (body: Partial<AgentSettings>) =>
    send<AgentSettings & { ok: boolean }>("/client/api/agent-settings", "PUT", body),
  putAgentProviderKeys: (body: {
    gemini_api_key?: string | null;
    groq_api_key?: string | null;
  }) =>
    send<{
      ok: boolean;
      has_gemini_api_key: boolean;
      has_groq_api_key: boolean;
      updated_at: string | null;
    }>("/client/api/agent-provider-keys", "PUT", body),

  getSiteGraph: () =>
    get<{
      yaml: string;
      revision: number;
      site: string;
      published: boolean;
      published_revision: number | null;
    }>("/client/api/site-graph"),
  putSiteGraph: (yaml: string) =>
    send<{ ok: boolean; revision: number; site: string; published: boolean }>(
      "/client/api/site-graph",
      "PUT",
      { yaml },
    ),
  publishSiteGraph: (revision?: number) =>
    send<{ ok: boolean; published_revision: number }>(
      "/client/api/site-graph/publish",
      "POST",
      { revision: revision ?? null },
    ),

  getDemoScript: (flowId?: string) =>
    get<DemoScriptResponse>(
      flowId
        ? `/client/api/site-graph/demo-script?flow_id=${encodeURIComponent(flowId)}`
        : "/client/api/site-graph/demo-script",
    ),
  patchDemoScript: (beats: DemoScriptBeat[]) =>
    send<DemoScriptResponse & { ok: boolean }>(
      "/client/api/site-graph/demo-script",
      "PATCH",
      { beats },
    ),
  regenerateDemoScript: (flowId?: string) =>
    send<DemoScriptResponse & { ok: boolean }>(
      flowId
        ? `/client/api/site-graph/demo-script/regenerate?flow_id=${encodeURIComponent(flowId)}`
        : "/client/api/site-graph/demo-script/regenerate",
      "POST",
    ),

  getFlows: () => get<{ playlist: Flow[]; site: string }>("/client/api/flows"),
  putFlows: (playlist: Flow[]) =>
    send<{ playlist: Flow[] }>("/client/api/flows", "PUT", { playlist }),
  deleteFlow: (flow_id: string, page_id?: string | null) =>
    send<{ playlist: Flow[]; deleted_flow_id: string }>(
      "/client/api/flows/delete",
      "POST",
      { flow_id, page_id: page_id || null },
    ),
  clearAllFlows: () =>
    send<{ playlist: Flow[]; revision: number; yaml: string; cleared: boolean }>(
      "/client/api/flows/clear",
      "POST",
    ),
  clearSiteGraph: () =>
    send<{
      yaml: string;
      revision: number;
      site: string;
      playlist: Flow[];
      cleared: boolean;
    }>("/client/api/site-graph/clear", "POST"),
  patchFlowSemantics: (body: {
    flow_id: string;
    purpose?: string;
    tags?: string[];
    auto_name?: string;
  }) =>
    send<{ playlist: Flow[]; semantics: Record<string, unknown> }>(
      "/client/api/flows/semantics",
      "PATCH",
      body,
    ),

  recordStatus: () => get<RecorderStatus>("/client/api/record"),
  recordStart: (
    start_url: string,
    flow_name: string,
    opts?: {
      narrate?: boolean;
      save_mode?: "new" | "update";
      target_flow_id?: string;
      target_flow_name?: string;
    },
  ) =>
    send<{ narrate?: boolean; save_mode?: string; flow_id?: string }>(
      "/client/api/record/start",
      "POST",
      {
        start_url,
        flow_name,
        narrate: opts?.narrate ?? false,
        save_mode: opts?.save_mode ?? "new",
        target_flow_id: opts?.target_flow_id,
        target_flow_name: opts?.target_flow_name,
      },
    ),
  recordCapture: () =>
    send<{ ok: boolean; phase: string; setup_discarded: number; steps: number }>(
      "/client/api/record/capture",
      "POST",
    ),
  recordStop: () =>
    send<{
      steps: number;
      error: string | null;
      flagged?: Array<{ tool?: string; selector?: string; reason?: string }>;
      setup_discarded?: number;
      narrated_steps?: number;
    }>("/client/api/record/stop", "POST"),

  exploreStatus: () => get<ExploreStatus>("/client/api/explore"),
  exploreFrame: () =>
    get<{ mime: string; data: string }>("/client/api/explore/frame"),
  exploreStart: (body: {
    base_url?: string | null;
    max_pages?: number;
    max_steps?: number;
    max_wall_clock_s?: number;
    save_mode?: "new" | "update";
    target_flow_id?: string | null;
    target_flow_name?: string | null;
    new_flow_name?: string | null;
    focus_hint?: string | null;
    include_paths?: string[];
    exclude_paths?: string[];
    exclude_labels?: string[];
  }) => send<ExploreStatus>("/client/api/explore/start", "POST", body),
  exploreStop: () => send<ExploreStatus>("/client/api/explore/stop", "POST"),
  exploreAnswer: (qid: string, value: string, skip = false) =>
    send<{ ok: boolean }>("/client/api/explore/answer", "POST", { qid, value, skip }),
  exploreFlagged: (body: {
    action: "allow" | "dismiss";
    selector?: string;
    label?: string;
    element_key?: string;
  }) => send<ExploreStatus>("/client/api/explore/flagged", "POST", body),
  exploreTicket: () =>
    send<{ ticket: string; expires_in_s: number }>("/client/api/explore/ticket", "POST"),
};

/** WebSocket URL for the live exploration log. Ticket is single-use. */
export async function exploreSocketUrl(): Promise<string> {
  const { ticket } = await api.exploreTicket();
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/client/api/explore/ws?ticket=${encodeURIComponent(ticket)}`;
}

export const slugKey = (label: string): string =>
  label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "") || "field";
