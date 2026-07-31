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

from navigator.config.site_graph import SiteGraph, SiteGraphError
from navigator.schemas import (
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


def click_element(
    page: Page, graph: SiteGraph, page_id: str, call: ClickElement
) -> tuple[str, str]:
    css = graph.selector(page_id, call.selector)
    page.click(css, timeout=call.expects.timeout_ms)
    return f"clicked {call.selector} ({css})", page_id


def fill_field(
    page: Page, graph: SiteGraph, page_id: str, call: FillField
) -> tuple[str, str]:
    css = graph.selector(page_id, call.selector)
    page.fill(css, call.value, timeout=call.expects.timeout_ms)
    return f"filled {call.selector} with {call.value!r} (source={call.source})", page_id


def navigate(
    page: Page, graph: SiteGraph, page_id: str, call: Navigate
) -> tuple[str, str]:
    url = graph.url_for(call.page_id)
    page.goto(url, timeout=call.expects.timeout_ms)
    return f"navigated to {call.page_id} ({url})", call.page_id


def wait_for(
    page: Page, graph: SiteGraph, page_id: str, call: WaitFor
) -> tuple[str, str]:
    css = graph.selector(page_id, call.selector)
    page.wait_for_selector(css, timeout=call.timeout_ms, state="visible")
    return f"{call.selector} appeared", page_id


_HANDLERS: dict[str, Callable[..., tuple[str, str]]] = {
    "click_element": click_element,
    "fill_field": fill_field,
    "navigate": navigate,
    "wait_for": wait_for,
}
