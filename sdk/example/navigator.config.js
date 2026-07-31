/**
 * A worked example of what a customer writes.
 *
 * Note what is absent: no CSS selectors. Every alias below resolves to
 * `[data-nav="<alias>"]`, which is what the annotated components expose. Compare
 * against tests/fixtures/crm_dashboard.html to see the other half.
 *
 * Written as .js so it runs with no TypeScript loader; a real customer would use
 * .ts and get completion on every builder.
 */

import {
  click,
  defineConfig,
  expectText,
  expectVisible,
  fillAndCheck,
  flow,
  navigate,
  waitFor,
} from "../dist/index.js";

export default defineConfig({
  product: "acme-inbox",
  version: 1,
  // Overridden by NAVIGATOR_EXAMPLE_BASE_URL so the round-trip test can point
  // this at the local fixture. A real config hardcodes the app's URL.
  baseUrl: process.env.NAVIGATOR_EXAMPLE_BASE_URL ?? "https://app.acme.com",
  persona: {
    product_name: "Acme Inbox",
    one_liner: "a shared inbox for support teams",
    agent_name: "Navigator",
  },
  pages: {
    inbox: {
      name: "Inbox",
      url: "crm_dashboard.html",
      flows: {
        send_message: flow(
          navigate("inbox", expectVisible("composer")),
          waitFor("message_input", expectVisible("message_input"), 3000),
          fillAndCheck("message_input", "Hi from the Navigator SDK"),
          click("send_button", expectText("sent_bubble", "Hi from the Navigator SDK")),
        ),

        // Deliberately broken, to prove `navigator verify` exits non-zero.
        // A real config would not contain this.
        broken_on_purpose: flow(
          navigate("inbox", expectVisible("composer")),
          click("ghost_button", expectVisible("ghost_button")),
        ),
      },
    },
  },
});
