import assert from "node:assert/strict";
import { test } from "node:test";

import {
  click,
  compile,
  dataNavSelector,
  expectCount,
  expectText,
  expectUrl,
  expectValue,
  expectVisible,
  fill,
  fillAndCheck,
  flow,
  navigate,
  resolveSelectors,
  waitFor,
} from "../dist/index.js";

const CONFIG = {
  product: "acme-inbox",
  baseUrl: "https://app.acme.test",
  persona: { product_name: "Acme Inbox", one_liner: "shared inbox" },
  pages: {
    inbox: {
      name: "Inbox",
      url: "/inbox",
      flows: {
        send_message: flow(
          navigate("inbox", expectVisible("composer")),
          fillAndCheck("message_input", "Hi there"),
          click("send_button", expectText("sent_bubble", "Hi there")),
        ),
      },
    },
  },
};

test("undeclared aliases resolve to data-nav", () => {
  const selectors = resolveSelectors(CONFIG.pages.inbox);
  assert.equal(selectors.send_button, '[data-nav="send_button"]');
  assert.equal(selectors.composer, '[data-nav="composer"]');
  assert.equal(dataNavSelector("x"), '[data-nav="x"]');
});

test("explicit selectors override data-nav", () => {
  const page = {
    ...CONFIG.pages.inbox,
    selectors: { send_button: "#legacy-send" },
  };
  const selectors = resolveSelectors(page);
  assert.equal(selectors.send_button, "#legacy-send", "declaration must win");
  assert.equal(selectors.composer, '[data-nav="composer"]', "others still inferred");
});

test("postcondition selectors are collected too", () => {
  // sent_bubble appears only inside expectText, never as a call target.
  assert.ok("sent_bubble" in resolveSelectors(CONFIG.pages.inbox));
});

test("compiled yaml carries the site graph fields the server expects", () => {
  const yaml = compile(CONFIG);
  assert.match(yaml, /^version: 1$/m);
  assert.match(yaml, /^site: "acme-inbox"$/m);
  assert.match(yaml, /^base_url: "https:\/\/app\.acme\.test"$/m);
  assert.match(yaml, /product_name: "Acme Inbox"/);
  assert.match(yaml, /send_message:/);
  assert.match(yaml, /- tool: navigate/);
  assert.match(yaml, /- tool: fill_field/);
  assert.match(yaml, /- tool: click_element/);
});

test("every call emits its postcondition", () => {
  const yaml = compile(CONFIG);
  const calls = (yaml.match(/- tool:/g) ?? []).length;
  const expects = (yaml.match(/^\s+expects:$/gm) ?? []).length;
  assert.equal(calls, 3);
  assert.equal(expects, 3, "a call without an expectation is the bug we prevent");
});

test("fill defaults to source agent and honours user", () => {
  const yaml = compile({
    ...CONFIG,
    pages: {
      inbox: {
        name: "Inbox",
        flows: {
          f: flow(
            fill("a", "1", expectValue("a", "1")),
            fill("b", "2", expectValue("b", "2"), "user"),
          ),
        },
      },
    },
  });
  assert.match(yaml, /source: agent/);
  assert.match(yaml, /source: user/);
});

test("selectors containing yaml metacharacters survive quoting", () => {
  const yaml = compile({
    product: "p",
    baseUrl: "https://x.test",
    pages: {
      main: {
        name: "Main",
        selectors: {
          weird: '#id > .cls:nth-child(2) [attr="v"]',
          starred: "*",
        },
        flows: { f: flow(click("weird", expectVisible("starred"))) },
      },
    },
  });
  assert.match(yaml, /weird: "#id > \.cls:nth-child\(2\) \[attr=\\"v\\"\]"/);
  assert.match(yaml, /starred: "\*"/);
});

test("url_matches emits no selector", () => {
  const yaml = compile({
    product: "p",
    baseUrl: "https://x.test",
    pages: {
      main: { name: "M", flows: { f: flow(navigate("main", expectUrl("/done"))) } },
    },
  });
  assert.match(yaml, /check: url_matches/);
  assert.match(yaml, /expected: "\/done"/);
  const expectsBlock = yaml.slice(yaml.indexOf("expects:"));
  assert.ok(!expectsBlock.includes("selector:"), "url_matches needs no selector");
});

test("element_count stringifies its number", () => {
  const yaml = compile({
    product: "p",
    baseUrl: "https://x.test",
    pages: {
      main: { name: "M", flows: { f: flow(click("row", expectCount("row", 3))) } },
    },
  });
  assert.match(yaml, /check: element_count/);
  assert.match(yaml, /expected: "3"/);
});

test("wait_for carries its own timeout", () => {
  const yaml = compile({
    product: "p",
    baseUrl: "https://x.test",
    pages: {
      main: {
        name: "M",
        flows: { f: flow(waitFor("spinner", expectVisible("spinner"), 2500)) },
      },
    },
  });
  assert.match(yaml, /timeout_ms: 2500/);
});

test("a page with no flows still emits a selectors key", () => {
  const yaml = compile({
    product: "p",
    baseUrl: "https://x.test",
    pages: { empty: { name: "Empty", flows: {} } },
  });
  assert.match(yaml, /selectors: \{\}/);
});

test("persona is optional", () => {
  const yaml = compile({
    product: "p",
    baseUrl: "https://x.test",
    pages: { main: { name: "M", flows: {} } },
  });
  assert.ok(!yaml.includes("persona:"));
});
