/**
 * @navigator/sdk — author Navigator AI demo flows in your own codebase.
 *
 * Three levels, each usable on its own:
 *
 *   1. Annotate. Put `data-nav="send_button"` on the elements the agent touches.
 *      Aliases resolve to that attribute automatically, so your selectors survive
 *      redesigns and CSS-module hashes.
 *
 *   2. Declare. Write `navigator.config.ts` next to the feature it demos, in the
 *      same PR, so the demo can't drift from the product.
 *
 *   3. Verify. Run `navigator verify` in CI. A broken demo fails the build
 *      instead of failing in front of a prospect.
 *
 * @example
 * ```ts
 * import { defineConfig, flow, navigate, fillAndCheck, click, expectText, expectVisible }
 *   from "@navigator/sdk";
 *
 * export default defineConfig({
 *   product: "acme-inbox",
 *   baseUrl: "https://app.acme.com",
 *   persona: { product_name: "Acme Inbox", one_liner: "shared inbox for support" },
 *   pages: {
 *     inbox: {
 *       name: "Inbox",
 *       url: "/inbox",
 *       flows: {
 *         send_message: flow(
 *           navigate("inbox", expectVisible("composer")),
 *           fillAndCheck("message_input", "Hi from Navigator"),
 *           click("send_button", expectText("sent_bubble", "Hi from Navigator")),
 *         ),
 *       },
 *     },
 *   },
 * });
 * ```
 */

export { defineConfig } from "./config.js";
export type {
  CheckKind,
  NavigatorConfig,
  PageConfig,
  Persona,
  Postcondition,
  Source,
  ToolCall,
} from "./config.js";

export {
  click,
  expectCount,
  expectHidden,
  expectText,
  expectUrl,
  expectValue,
  expectVisible,
  fill,
  fillAndCheck,
  flow,
  navigate,
  waitFor,
} from "./dsl.js";

export { compile, dataNavSelector, referencedAliases, resolveSelectors } from "./compile.js";

export { NavigatorApiError, NavigatorClient } from "./client.js";
export type { ActionLogEntry, DemoView, SiteGraphRevision } from "./client.js";

// The Fern-generated client is re-exported here once `fern generate` has run.
// It provides the full typed surface for every endpoint in our OpenAPI schema,
// whereas NavigatorClient above covers only what the CLI needs and has no
// generated code behind it.
//
// TODO(phase 7): uncomment after running `npx fern-api generate` (needs FERN_TOKEN):
// export * as api from "./generated/index.js";
