/**
 * Thin client over the Navigator API.
 *
 * Hand-written on purpose, covering only the endpoints the CLI needs. The
 * Fern-generated client (from our OpenAPI schema) is re-exported from index.ts for
 * customers who want the full typed surface; this exists so `navigator push` and
 * `navigator verify` work with zero generated code and zero runtime dependencies.
 *
 * Uses global fetch (node >= 18). No axios, no node-fetch.
 */

export interface SiteGraphRevision {
  product_id: string;
  revision: number;
  source: "yaml" | "recorded" | "sdk";
  site: string;
  graph_version: number;
  created_at: string;
}

export interface DemoView {
  demo_id: string;
  product_id: string;
  revision: number;
  session_id: string;
  status: "starting" | "running" | "finished" | "failed";
  page_id: string;
  actions: number;
  failures: number;
  error: string | null;
  said: string[];
}

export interface ActionLogEntry {
  page: string;
  tool_call: { tool: string; [k: string]: unknown };
  expected_postcondition: { check: string; selector?: string; expected?: string };
  actual_result: { ok: boolean; detail: string };
  verify: { passed: boolean; actual: string; ambiguous: boolean } | null;
}

/** An API error carrying the server's own message, not a generic status string. */
export class NavigatorApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status}: ${detail}`);
    this.name = "NavigatorApiError";
  }
}

export class NavigatorClient {
  constructor(
    private readonly baseUrl: string,
    private readonly apiKey: string,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  static fromEnv(): NavigatorClient {
    const key = process.env.NAVIGATOR_API_KEY;
    if (!key) {
      throw new Error(
        "NAVIGATOR_API_KEY is not set. Get one from POST /v1/products.",
      );
    }
    return new NavigatorClient(
      process.env.NAVIGATOR_BASE_URL ?? "http://localhost:8000",
      key,
    );
  }

  async pushSiteGraph(yaml: string): Promise<SiteGraphRevision> {
    return this.request("PUT", "/v1/products/site-graph", {
      yaml,
      source: "sdk",
    });
  }

  async startDemo(pageId: string, flowId: string): Promise<DemoView> {
    return this.request("POST", "/v1/demos", {
      page_id: pageId,
      flow_id: flowId,
    });
  }

  async getDemo(demoId: string): Promise<DemoView> {
    return this.request("GET", `/v1/demos/${demoId}`);
  }

  async demoActions(demoId: string): Promise<ActionLogEntry[]> {
    return this.request("GET", `/v1/demos/${demoId}/actions`);
  }

  async flows(): Promise<Record<string, string[]>> {
    return this.request("GET", "/v1/products/flows");
  }

  /** Poll until the demo leaves a running state. */
  async waitForDemo(demoId: string, timeoutMs = 120_000): Promise<DemoView> {
    const deadline = Date.now() + timeoutMs;
    let demo = await this.getDemo(demoId);
    while (demo.status === "starting" || demo.status === "running") {
      if (Date.now() > deadline) {
        throw new Error(
          `demo ${demoId} still ${demo.status} after ${timeoutMs}ms`,
        );
      }
      await new Promise((r) => setTimeout(r, 1000));
      demo = await this.getDemo(demoId);
    }
    return demo;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: {
        Authorization: `Token ${this.apiKey}`,
        ...(body !== undefined && { "Content-Type": "application/json" }),
      },
      ...(body !== undefined && { body: JSON.stringify(body) }),
    });

    const text = await response.text();
    if (!response.ok) {
      // Surface the server's `detail` -- for a rejected site graph that is the
      // validator's exact message, which is the whole value of the round trip.
      let detail = text;
      try {
        const parsed = JSON.parse(text);
        detail = typeof parsed.detail === "string" ? parsed.detail : text;
      } catch {
        // non-JSON error body; use the raw text
      }
      throw new NavigatorApiError(response.status, detail);
    }
    return text ? (JSON.parse(text) as T) : (undefined as T);
  }
}
