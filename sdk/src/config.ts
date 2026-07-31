/**
 * The shape of a `navigator.config.ts`.
 *
 * These types mirror the Python Pydantic models one-for-one. They exist to give
 * the customer completion and compile-time errors while authoring -- they are NOT
 * a validator. Every rule is enforced by the server's single site-graph validator
 * when the config is pushed.
 */

/** Which postcondition checks the agent can assert. Mirrors Python's CheckKind. */
export type CheckKind =
  | "visible"
  | "hidden"
  | "text_contains"
  | "value_equals"
  | "url_matches"
  | "element_count";

export interface Postcondition {
  check: CheckKind;
  /** Selector alias, never raw CSS. Omitted only for `url_matches`. */
  selector?: string;
  expected?: string;
  timeout_ms?: number;
}

/** Marks data the prospect supplied live during the call. */
export type Source = "agent" | "user";

export type ToolCall =
  | { tool: "click_element"; selector: string; expects: Postcondition }
  | {
      tool: "fill_field";
      selector: string;
      value: string;
      source?: Source;
      expects: Postcondition;
    }
  | { tool: "navigate"; page_id: string; expects: Postcondition }
  | {
      tool: "wait_for";
      selector: string;
      timeout_ms?: number;
      expects: Postcondition;
    };

/** How the agent introduces this product on a call. */
export interface Persona {
  product_name: string;
  one_liner?: string;
  agent_name?: string;
  tone?: string;
}

export interface PageConfig {
  /** Human label. Spoken in narration, so write it the way you'd say it. */
  name: string;
  /** Resolved against `baseUrl`. */
  url?: string;
  /**
   * alias -> CSS selector.
   *
   * Usually omitted: any alias used in a flow but absent here resolves to
   * `[data-nav="<alias>"]`, which is the whole point of annotating your
   * components. Add an entry only to override that.
   */
  selectors?: Record<string, string>;
  flows: Record<string, ToolCall[]>;
}

export interface NavigatorConfig {
  /** Product slug. Must match the product you registered. */
  product: string;
  /** Bumped by you when selectors change, so logs stay attributable. */
  version?: number;
  /** Absolute base URL of the app being demoed. */
  baseUrl: string;
  persona?: Persona;
  pages: Record<string, PageConfig>;
}

/** Identity helper so a config file gets type checking without a cast. */
export function defineConfig(config: NavigatorConfig): NavigatorConfig {
  return config;
}
