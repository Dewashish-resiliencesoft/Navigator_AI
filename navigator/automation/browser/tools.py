"""The agent's entire vocabulary for touching the browser.

Four tools, no free-form DOM access. Callers pass a selector *alias*; the alias is
resolved through the site graph here, so no caller -- including a Phase 2 LLM --
can invent a selector.

Every tool returns a ToolResult and never raises: a Playwright timeout is a result
with ok=False, because a failed action is data the ActionLog needs, not an
exception that kills the call.
"""

from __future__ import annotations

import time
from typing import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from navigator.knowledge.site_graph import SiteGraph, SiteGraphError
from navigator.core.schemas import (
    ClickElement,
    FillField,
    Navigate,
    ToolCall,
    ToolResult,
    WaitFor,
)


def execute(
    page: Page, graph: SiteGraph, page_id: str, call: ToolCall
) -> tuple[ToolResult, str]:
    """Run one tool call.

    Returns the result plus the page_id in effect afterwards -- a navigate moves
    the agent, and VERIFYING needs to resolve its postcondition against wherever
    it landed.
    """
    handler = _HANDLERS[call.tool]
    started = time.perf_counter()
    try:
        detail, next_page_id = handler(page, graph, page_id, call)
        ok = True
    except (PlaywrightError, SiteGraphError) as exc:
        detail, next_page_id, ok = _describe(exc), page_id, False

    return (
        ToolResult(
            ok=ok,
            tool=call.tool,
            detail=detail,
            duration_ms=int((time.perf_counter() - started) * 1000),
        ),
        next_page_id,
    )


def _describe(exc: Exception) -> str:
    """First line of an exception. Playwright messages carry a full log otherwise."""
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


# -- individual tools ---------------------------------------------------------
# Each returns (detail, next_page_id) and lets exceptions escape to execute().


def _action_timeout(ms: int) -> int:
    """Real product UIs often need >5s; keep sub-1s timeouts for unit tests."""
    if ms < 1000:
        return ms
    return max(ms, 15000)


def click_element(
    page: Page, graph: SiteGraph, page_id: str, call: ClickElement
) -> tuple[str, str]:
    css = graph.selector(page_id, call.selector)
    timeout = _action_timeout(call.expects.timeout_ms)
    from navigator.automation.browser.cursor import click_with_cursor

    click_with_cursor(page, css, timeout=timeout)
    return f"clicked {call.selector} ({css})", page_id


def fill_field(
    page: Page, graph: SiteGraph, page_id: str, call: FillField
) -> tuple[str, str]:
    css = graph.selector(page_id, call.selector)
    timeout = _action_timeout(call.expects.timeout_ms)
    from navigator.automation.browser.cursor import (
        PAUSE_AFTER_CLICK_MS,
        _clear_highlight,
        _wait_ms,
        guide_to,
    )

    guide_to(page, css, timeout=timeout, highlight=True)
    page.locator(css).first.fill(call.value, timeout=timeout)
    _clear_highlight(page)
    _wait_ms(page, PAUSE_AFTER_CLICK_MS)
    return f"filled {call.selector} with {call.value!r} (source={call.source})", page_id


def navigate(
    page: Page, graph: SiteGraph, page_id: str, call: Navigate
) -> tuple[str, str]:
    url = graph.url_for(call.page_id)
    page.goto(url, timeout=call.expects.timeout_ms)
    try:
        from navigator.automation.browser.cursor import install_cursor

        install_cursor(page)
    except Exception:  # noqa: BLE001
        pass
    return f"navigated to {call.page_id} ({url})", call.page_id


def wait_for(
    page: Page, graph: SiteGraph, page_id: str, call: WaitFor
) -> tuple[str, str]:
    css = graph.selector(page_id, call.selector)
    page.wait_for_selector(
        css, timeout=_action_timeout(call.timeout_ms), state="visible"
    )
    return f"{call.selector} appeared", page_id


_HANDLERS: dict[str, Callable[..., tuple[str, str]]] = {
    "click_element": click_element,
    "fill_field": fill_field,
    "navigate": navigate,
    "wait_for": wait_for,
}
