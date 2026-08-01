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

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text };
  }
  if (!res.ok) {
    const detail =
      (body as { detail?: unknown; message?: unknown })?.detail ??
      (body as { message?: unknown })?.message ??
      text ??
      res.statusText;
    throw new ApiError(
      typeof detail === "string" ? detail : JSON.stringify(detail),
      res.status,
    );
  }
  return body as T;
}

/** The server can lose its client key across a --reload; bootstrap re-mints it. */
function needsBootstrap(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  if (err.status === 503) return true;
  return /CLIENT_API_KEY|bootstrap|ops/i.test(err.message);
}

async function withBootstrap<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call();
  } catch (err) {
    if (!needsBootstrap(err)) throw err;
    await request("/client/api/bootstrap", { method: "POST", body: "{}" });
    return call();
  }
}

const get = <T>(path: string) => withBootstrap(() => request<T>(path));
const send = <T>(path: string, method: string, body?: unknown) =>
  withBootstrap(() =>
    request<T>(path, { method, body: JSON.stringify(body ?? {}) }),
  );

export type StartDemoBody = {
  platform: string;
  intake: {
    name: string;
    company: string;
    business_type: string;
    looking_for: string;
  };
};

export const api = {
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

  getBio: () => get<{ fields: BioField[] }>("/client/api/bio"),
  putBio: (fields: BioField[]) => send<unknown>("/client/api/bio", "PUT", { fields }),

  getKnowledge: () => get<{ markdown: string }>("/client/api/knowledge"),
  putKnowledge: (markdown: string) =>
    send<unknown>("/client/api/knowledge", "PUT", { markdown }),

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
