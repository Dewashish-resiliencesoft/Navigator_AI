export type DemoStatus = "starting" | "running" | "finished" | "failed";

export type Demo = {
  demo_id: string;
  product_id: string;
  revision: number;
  session_id: string;
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
export type Flow = { name: string; page_id: string; flow_id: string; order?: number };

export type RecorderStatus = {
  active?: boolean;
  recording?: boolean;
  status?: string;
  steps?: number;
  flow_name?: string | null;
  error?: string | null;
};

export type MetricPoint = {
  day: string;
  actions: number;
  sessions: number;
  failures: number;
};

export type Metrics = {
  actions: number;
  sessions: number;
  failures: number;
  verified: number;
  passed: number;
  last_seen: string | null;
  series: MetricPoint[];
  live: { total: number; running: number; failed: number };
};

export type DemoRun = {
  session_id: string;
  demo_id: string;
  product_id: string;
  platform: string;
  status: string;
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
  bootstrap: () =>
    request<{ ok: boolean; product_id: string; api_key: string | null; message: string }>(
      "/client/api/bootstrap",
      { method: "POST", body: "{}" },
    ),

  listDemos: () => get<Demo[]>("/client/api/demos"),
  getDemo: (id: string) => get<Demo>(`/client/api/demos/${id}`),
  startDemo: (body: StartDemoBody) =>
    send<Demo>("/client/api/demos/start", "POST", body),
  endDemo: (id: string) => send<Demo>(`/client/api/demos/${id}/end`, "POST"),

  metrics: (days = 14) => get<Metrics>(`/client/api/metrics?days=${days}`),

  listRuns: (days = 7) => get<DemoRun[]>(`/client/api/runs?days=${days}`),
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

  getSiteGraph: () =>
    get<{ yaml: string; revision: number; site: string }>("/client/api/site-graph"),
  putSiteGraph: (yaml: string) =>
    send<{ ok: boolean; revision: number; site: string }>(
      "/client/api/site-graph",
      "PUT",
      { yaml },
    ),

  getFlows: () => get<{ playlist: Flow[]; site: string }>("/client/api/flows"),
  putFlows: (playlist: Flow[]) =>
    send<{ playlist: Flow[] }>("/client/api/flows", "PUT", { playlist }),

  recordStatus: () => get<RecorderStatus>("/client/api/record"),
  recordStart: (start_url: string, flow_name: string) =>
    send<unknown>("/client/api/record/start", "POST", { start_url, flow_name }),
  recordStop: () => send<{ steps: number; error: string | null }>(
    "/client/api/record/stop",
    "POST",
  ),
};

export const slugKey = (label: string): string =>
  label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "") || "field";
