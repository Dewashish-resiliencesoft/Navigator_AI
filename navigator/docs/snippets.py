"""Literal code samples for the docs.

These are prose, not extracted from code, because they are teaching examples rather
than reflections of the schema. Kept in one module so they are reviewable as a set
and appear identically in the HTML and the Fern docs.

The runnable parts are covered by tests elsewhere: `site_graph_yaml` mirrors the
seed graph, and `sdk_config` mirrors sdk/example/navigator.knowledge.js, which the
SDK's own test suite compiles and pushes.
"""

from __future__ import annotations

QUICKSTART_CURL = """# 1. Register. The api_key comes back once — store it as a secret.
curl -X POST https://your-navigator/v1/products \\
     -H 'Content-Type: application/json' \\
     -d '{"name": "Acme Inbox"}'

export NAVIGATOR_API_KEY=nav_...

# 2. Upload a site graph for your product.
curl -X PUT https://your-navigator/v1/products/site-graph \\
     -H "Authorization: Token $NAVIGATOR_API_KEY" \\
     -H 'Content-Type: application/json' \\
     -d "{\\"yaml\\": $(jq -Rs . < navigator.yaml)}"

# 3. Run a demo, then poll it.
curl -X POST https://your-navigator/v1/demos \\
     -H "Authorization: Token $NAVIGATOR_API_KEY" \\
     -H 'Content-Type: application/json' \\
     -d '{"page_id": "inbox", "flow_id": "send_message"}'

curl https://your-navigator/v1/demos/$DEMO_ID \\
     -H "Authorization: Token $NAVIGATOR_API_KEY"
"""

SITE_GRAPH_YAML = """version: 1
site: acme-inbox
base_url: https://app.acme.com/

# How the agent introduces your product. No narration lives in our code.
persona:
  product_name: Acme Inbox
  one_liner: a shared inbox for support teams
  agent_name: Navigator

pages:
  inbox:
    name: Inbox              # spoken aloud, so write it how you'd say it
    url: /inbox              # resolved against base_url
    selectors:               # alias -> selector. Flows only ever use aliases.
      composer: "[data-nav='composer']"
      message_input: "#message-input"
      send_button: "#send-btn"
      sent_bubble: ".message.sent"

    flows:
      send_message:
        - tool: navigate
          page_id: inbox
          expects: {check: visible, selector: composer}

        - tool: fill_field
          selector: message_input
          value: "Hi from Navigator"
          source: agent        # use "user" for data a prospect gave you live
          expects:
            check: value_equals
            selector: message_input
            expected: "Hi from Navigator"

        - tool: click_element
          selector: send_button
          expects:
            check: text_contains
            selector: sent_bubble
            expected: "Hi from Navigator"
"""

SDK_INSTALL = """npm install --save-dev @navigator/sdk

export NAVIGATOR_API_KEY=nav_...
export NAVIGATOR_BASE_URL=https://your-navigator

npx navigator compile   # print the generated site graph YAML
npx navigator push      # upload it as a new revision
npx navigator verify    # run every flow; exit 1 if any postcondition fails
"""

ANNOTATE_JSX = """// Your component. One attribute per element the agent touches.
<div data-nav="composer">
  <input data-nav="message_input" placeholder="Type a message" />
  <button data-nav="send_button" onClick={send}>Send</button>
</div>

// The alias `send_button` now resolves to [data-nav="send_button"].
// Restyle, rename your CSS modules, swap component libraries — the demo
// keeps working, because the attribute travels with the element.
"""

SDK_CONFIG = """// navigator.knowledge.ts
import {
  defineConfig, flow, navigate, fillAndCheck, click, waitFor,
  expectVisible, expectText,
} from "@navigator/sdk";

export default defineConfig({
  product: "acme-inbox",
  baseUrl: "https://app.acme.com",
  persona: {
    product_name: "Acme Inbox",
    one_liner: "a shared inbox for support teams",
  },
  pages: {
    inbox: {
      name: "Inbox",
      url: "/inbox",
      // No `selectors` block: every alias below resolves to its data-nav
      // attribute. Add one only to override a specific element.
      flows: {
        send_message: flow(
          navigate("inbox", expectVisible("composer")),
          waitFor("message_input", expectVisible("message_input"), 3000),
          fillAndCheck("message_input", "Hi from Navigator"),
          click("send_button", expectText("sent_bubble", "Hi from Navigator")),
        ),
      },
    },
  },
});
"""

CI_YAML = """# .github/workflows/demo.yml
name: demo flows
on: [pull_request]

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20 }
      - run: npm ci
      - run: npm run start &        # your app, on a URL Navigator can reach
      - run: npx navigator verify
        env:
          NAVIGATOR_API_KEY: ${{ secrets.NAVIGATOR_API_KEY }}
          NAVIGATOR_BASE_URL: https://your-navigator
"""

VERIFY_OUTPUT = """$ npx navigator verify
PASS  inbox/send_message  4 action(s), 0 failure(s)
FAIL  inbox/onboard_user  2 action(s), 1 failure(s)
      click_element: expected visible on invite_button, got: [data-nav="invite_button"] not found

1/2 flow(s) passed
$ echo $?
1
"""

SNIPPETS: dict[str, str] = {
    "quickstart_curl": QUICKSTART_CURL,
    "site_graph_yaml": SITE_GRAPH_YAML,
    "sdk_install": SDK_INSTALL,
    "annotate_jsx": ANNOTATE_JSX,
    "sdk_config": SDK_CONFIG,
    "ci_yaml": CI_YAML,
    "verify_output": VERIFY_OUTPUT,
}
