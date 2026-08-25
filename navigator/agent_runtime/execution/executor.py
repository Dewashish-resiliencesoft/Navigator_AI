"""Resolve semantic actions → ToolCall → Playwright."""

from __future__ import annotations

from typing import Any, Callable

from navigator.agent_runtime.dom.builder import semantic_id_for
from navigator.agent_runtime.models import AgentAction, SemanticVerification
from navigator.automation.browser import tools as browser_tools
from navigator.automation.browser.verify import check as verify_check
from navigator.core.schemas import (
    ClickElement,
    FillField,
    Navigate,
    Postcondition,
    ToolCall,
    WaitFor,
)
from navigator.knowledge.site_graph import SiteGraph


def _page_aliases(graph: SiteGraph, page_id: str) -> list[str]:
    try:
        return list(graph.page(page_id).selectors.keys())
    except Exception:  # noqa: BLE001
        return []


def _match_alias(graph: SiteGraph, page_id: str, action: AgentAction) -> str | None:
    """Map semantic target to a site-graph selector alias on this page."""
    target = action.target
    aliases = _page_aliases(graph, page_id)
    if target.semantic_id:
        needle = target.semantic_id.replace("_", " ").lower()
        for alias in aliases:
            if needle in alias.lower().replace("_", " "):
                return alias
    if target.label:
        label = target.label.lower()
        for alias in aliases:
            if label in alias.lower():
                return alias
    return None


def _inventory_alias(page: Any, action: AgentAction) -> str | None:
    from navigator.automation.explore import perceive

    label = (action.target.label or "").strip().lower()
    sid = (action.target.semantic_id or "").strip().lower()
    for el in perceive.inventory(page):
        el_sid = semantic_id_for(el).lower()
        el_text = (el.get("text") or el.get("aria_label") or "").strip().lower()
        if sid and el_sid == sid:
            testid = el.get("testid") or el.get("id") or el.get("name")
            if testid:
                return str(testid)
        if label and label in el_text:
            testid = el.get("testid") or el.get("id") or el.get("name") or el_text[:40]
            return str(testid)
    return None


def semantic_to_tool_call(
    graph: SiteGraph,
    page_id: str,
    page: Any,
    action: AgentAction,
) -> ToolCall | None:
    verification = action.verification
    expects = _postcondition(verification, action)

    if action.tool == "navigate":
        pid = action.target.page_id or action.target.semantic_id or action.value
        if not pid:
            return None
        try:
            url_hint = graph.url_for(pid)
        except Exception:  # noqa: BLE001
            url_hint = pid
        expected = verification.expected if verification and verification.expected else url_hint
        return Navigate(page_id=pid, expects=expects or Postcondition(check="url_matches", expected=expected))

    alias = _match_alias(graph, page_id, action)
    if not alias:
        alias = _inventory_alias(page, action)
    if not alias:
        return None

    if action.tool in {"click", "hover", "scroll"}:
        return ClickElement(selector=alias, expects=expects or Postcondition(check="visible", selector=alias))
    if action.tool in {"type", "select"}:
        return FillField(
            selector=alias,
            value=action.value,
            expects=expects or Postcondition(check="value_equals", selector=alias, expected=action.value),
        )
    if action.tool == "wait":
        return WaitFor(selector=alias, expects=expects or Postcondition(check="visible", selector=alias))
    return ClickElement(selector=alias, expects=expects or Postcondition(check="visible", selector=alias))


def _postcondition(verification: SemanticVerification | None, action: AgentAction) -> Postcondition | None:
    if verification is None:
        return None
    if verification.check == "url_contains":
        return Postcondition(check="url_matches", expected=verification.expected)
    if verification.check == "visible":
        sel = verification.selector or action.target.semantic_id or "body"
        return Postcondition(check="visible", selector=sel)
    if verification.check == "text_contains":
        sel = verification.selector or action.target.semantic_id or "body"
        return Postcondition(check="text_contains", selector=sel, expected=verification.expected)
    if verification.check == "hidden":
        sel = verification.selector or action.target.semantic_id or "body"
        return Postcondition(check="hidden", selector=sel)
    return None


def execute_action(
    *,
    graph: SiteGraph,
    page: Any,
    page_id: str,
    action: AgentAction,
    on_frame: Callable[[], None] | None = None,
) -> tuple[ToolCall | None, Any, str, Any]:
    """Run one semantic action. Returns (call, tool_result, next_page_id, verify_result)."""
    call = semantic_to_tool_call(graph, page_id, page, action)
    if call is None:
        from navigator.core.schemas import ToolResult

        return (
            None,
            ToolResult(ok=False, tool=action.tool, detail="could not resolve semantic target"),
            page_id,
            None,
        )
    result, next_page = browser_tools.execute(page, graph, page_id, call, on_frame=on_frame)
    verify = verify_check(page, graph, next_page, call.expects)
    return call, result, next_page, verify
