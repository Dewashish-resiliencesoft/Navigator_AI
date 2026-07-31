/**
 * The authoring DSL.
 *
 * Every action requires a postcondition -- that is enforced by the type system
 * here, not by a lint rule, because a tool call without a declared expectation is
 * exactly the thing this whole system exists to prevent. If the agent can't say
 * what should happen, it can't tell you when it didn't.
 */

import type { Postcondition, Source, ToolCall } from "./config.js";

// --- postcondition builders --------------------------------------------------

/** Element is present and visible. */
export function expectVisible(selector: string, timeoutMs?: number): Postcondition {
  return { check: "visible", selector, ...(timeoutMs && { timeout_ms: timeoutMs }) };
}

/** Element is absent, or present but not visible. */
export function expectHidden(selector: string, timeoutMs?: number): Postcondition {
  return { check: "hidden", selector, ...(timeoutMs && { timeout_ms: timeoutMs }) };
}

/** Element's text contains `expected`. */
export function expectText(
  selector: string,
  expected: string,
  timeoutMs?: number,
): Postcondition {
  return {
    check: "text_contains",
    selector,
    expected,
    ...(timeoutMs && { timeout_ms: timeoutMs }),
  };
}

/** Input's value equals `expected` exactly. */
export function expectValue(
  selector: string,
  expected: string,
  timeoutMs?: number,
): Postcondition {
  return {
    check: "value_equals",
    selector,
    expected,
    ...(timeoutMs && { timeout_ms: timeoutMs }),
  };
}

/** Page URL contains `expected`. The only check that needs no selector. */
export function expectUrl(expected: string): Postcondition {
  return { check: "url_matches", expected };
}

/** Exactly `count` visible elements match. */
export function expectCount(
  selector: string,
  count: number,
  timeoutMs?: number,
): Postcondition {
  return {
    check: "element_count",
    selector,
    expected: String(count),
    ...(timeoutMs && { timeout_ms: timeoutMs }),
  };
}

// --- tool builders -----------------------------------------------------------

export function click(selector: string, expects: Postcondition): ToolCall {
  return { tool: "click_element", selector, expects };
}

/**
 * Type into a field.
 *
 * Pass `source: "user"` for anything a prospect gave you on the call -- it shows
 * up in the action log so a customer can see which data was theirs.
 */
export function fill(
  selector: string,
  value: string,
  expects: Postcondition,
  source: Source = "agent",
): ToolCall {
  return { tool: "fill_field", selector, value, source, expects };
}

export function navigate(pageId: string, expects: Postcondition): ToolCall {
  return { tool: "navigate", page_id: pageId, expects };
}

export function waitFor(
  selector: string,
  expects: Postcondition,
  timeoutMs?: number,
): ToolCall {
  return {
    tool: "wait_for",
    selector,
    ...(timeoutMs && { timeout_ms: timeoutMs }),
    expects,
  };
}

/** Sugar: `fill` whose postcondition is "the value I typed is in the field". */
export function fillAndCheck(
  selector: string,
  value: string,
  source: Source = "agent",
): ToolCall {
  return fill(selector, value, expectValue(selector, value), source);
}

/** Groups calls into a flow. Purely for readability at the call site. */
export function flow(...calls: ToolCall[]): ToolCall[] {
  return calls;
}
