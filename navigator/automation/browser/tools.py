"""The agent's entire vocabulary for touching the browser.

Five tools, no free-form DOM access. Callers pass a selector *alias*; the alias is
resolved through the site graph here, so no caller -- including a Phase 2 LLM --
can invent a selector.

Every tool returns a ToolResult and never raises: a Playwright timeout is a result
with ok=False, because a failed action is data the ActionLog needs, not an
exception that kills the call.
"""

from __future__ import annotations

import inspect
import time
from typing import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from navigator.knowledge.site_graph import SiteGraph, SiteGraphError
from navigator.core.schemas import (
    ClickElement,
    FillField,
    Navigate,
    ScrollPage,
    ToolCall,
    ToolResult,
    WaitFor,
)


# One action must not sit on the schema's 15s default. A step that still
# fails after the retries is returned, not left running.
ACTION_TIMEOUT_CAP_MS = 6000
NAV_TIMEOUT_CAP_MS = 8000
STEP_WATCHDOG_S = 18.0
_RETRYABLE_TOOLS = frozenset({"click_element", "fill_field", "navigate", "wait_for"})
_BACKOFF_S = (0.2, 0.5)


def execute(
    page: Page,
    graph: SiteGraph,
    page_id: str,
    call: ToolCall,
    *,
    on_frame: Callable[[], None] | None = None,
    mouse_path: list[dict[str, int]] | None = None,
) -> tuple[ToolResult, str]:
    """Run one tool call.

    Returns the result plus the page_id in effect afterwards -- a navigate moves
    the agent, and VERIFYING needs to resolve its postcondition against wherever
    it landed.

    ``on_frame`` is the live-demo frame pusher. Handlers that animate the cursor
    call it per micro-step so the screenshare shows motion, not a teleport.

    Click, fill, navigate, and wait retry at most twice, with a short backoff.
    The step watchdog marks a call that is still failing after ``STEP_WATCHDOG_S``.
    """
    from navigator.automation.browser.bounded import run_limited
    from navigator.automation.browser.gesture import begin_attempt, gesture_was_sent

    handler = _HANDLERS[call.tool]
    started = time.perf_counter()
    attempts = 3 if call.tool in _RETRYABLE_TOOLS else 1
    detail, next_page_id, ok = "", page_id, False
    _emit_browser_clock(call.tool, "start")
    try:
        for attempt in range(attempts):
            remaining = STEP_WATCHDOG_S - (time.perf_counter() - started)
            if STEP_WATCHDOG_S > 0 and remaining <= 0:
                detail = f"watchdog: {detail or 'step exceeded the hard limit'}"
                ok = False
                break
            begin_attempt()
            try:
                produced = handler(
                    page, graph, page_id, call, on_frame=on_frame, mouse_path=mouse_path
                )
                if inspect.isawaitable(produced):
                    limit = remaining if STEP_WATCHDOG_S > 0 else STEP_WATCHDOG_S
                    try:
                        produced = run_limited(produced, max(limit, 0.0))
                    except TimeoutError:
                        detail = "watchdog: step exceeded the hard limit"
                        ok = False
                        break
                detail, next_page_id = produced
                ok = True
                break
            except SiteGraphError as exc:
                detail, next_page_id, ok = _describe(exc), page_id, False
                break
            except PlaywrightError as exc:
                detail = _describe(exc)
                # A landed click/fill must not be sent again when only the
                # confirmation timed out.
                if gesture_was_sent():
                    next_page_id, ok = page_id, False
                    break
                if attempt + 1 < attempts and _retryable_action(exc):
                    pause = _BACKOFF_S[min(attempt, len(_BACKOFF_S) - 1)]
                    print(
                        f"[tool] {call.tool} retry {attempt + 1} in {pause:.1f}s: {detail[:140]}",
                        flush=True,
                    )
                    time.sleep(pause)
                    continue
                if attempt:
                    detail = f"retry exhausted: {detail}"
                next_page_id, ok = page_id, False
                break
        elapsed = time.perf_counter() - started
        if not ok and elapsed >= STEP_WATCHDOG_S:
            print(
                f"[step] watchdog {call.tool} still failing after {elapsed:.1f}s — skipping",
                flush=True,
            )
            detail = f"watchdog: {detail}"
        return (
            ToolResult(
                ok=ok,
                tool=call.tool,
                detail=detail,
                duration_ms=int(elapsed * 1000),
            ),
            next_page_id,
        )
    finally:
        _emit_browser_clock(call.tool, "end", ok=ok, detail=detail)


def _emit_browser_clock(
    tool: str,
    phase: str,
    *,
    ok: bool | None = None,
    detail: str = "",
) -> None:
    """Stamp this action on the shared monotonic clock. Never fails the tool."""
    try:
        from navigator.agent_runtime.sync_clock import emit_browser_action

        emit_browser_action(tool, phase, ok=ok, detail=detail)
    except Exception as exc:  # noqa: BLE001
        print(f"[tool] sync clock failed: {exc}", flush=True)


def _describe(exc: Exception) -> str:
    """First line of an exception. Playwright messages carry a full log otherwise."""
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


# -- individual tools ---------------------------------------------------------
# Each returns (detail, next_page_id) and lets exceptions escape to execute().


def _action_timeout(ms: int, *, navigate: bool = False) -> int:
    """Caller timeout, capped. Sub-1s values stay exact for unit tests."""
    cap = NAV_TIMEOUT_CAP_MS if navigate else ACTION_TIMEOUT_CAP_MS
    if ms < 1000:
        return ms
    return min(int(ms), cap)


def _retryable_action(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        bit in msg
        for bit in ("timeout", "not visible", "detached", "intercept", "net::")
    )


def click_element(
    page: Page,
    graph: SiteGraph,
    page_id: str,
    call: ClickElement,
    *,
    on_frame: Callable[[], None] | None = None,
    mouse_path: list[dict[str, int]] | None = None,
) -> tuple[str, str]:
    css = graph.selector(page_id, call.selector)
    timeout = _action_timeout(call.expects.timeout_ms)
    from navigator.automation.browser.viewport_awareness import bring_alias_into_view

    bring_alias_into_view(page, graph, page_id, call.selector)
    from navigator.automation.browser.cursor import click_with_cursor

    click_with_cursor(
        page, css, timeout=timeout, on_frame=on_frame, mouse_path=mouse_path
    )
    return f"clicked {call.selector} ({css})", page_id


def fill_field(
    page: Page,
    graph: SiteGraph,
    page_id: str,
    call: FillField,
    *,
    on_frame: Callable[[], None] | None = None,
    mouse_path: list[dict[str, int]] | None = None,
) -> tuple[str, str]:
    css = graph.selector(page_id, call.selector)
    timeout = _action_timeout(call.expects.timeout_ms)
    from navigator.automation.browser.viewport_awareness import bring_alias_into_view

    bring_alias_into_view(page, graph, page_id, call.selector)
    from navigator.automation.browser.cursor import (
        PAUSE_AFTER_CLICK_MS,
        _clear_highlight,
        _paced_wait,
        guide_to,
        replay_mouse_path,
    )

    if mouse_path:
        x, y = replay_mouse_path(page, mouse_path, on_frame=on_frame)
        # Focus the recorded point first so fill lands where the Client typed.
        page.mouse.click(x, y)
        from navigator.automation.browser.gesture import note_gesture_sent

        note_gesture_sent()
        # Duplicate ids (signup #email + sign-in #email) are common. After the
        # recorded click, type into :focus — not locator(css).first, which hits
        # the wrong form.
        target = _fill_target_after_point(page, css, timeout=timeout)
    else:
        guide_to(page, css, timeout=timeout, highlight=True, on_frame=on_frame)
        target = _visible_locator(page, css)
    from navigator.automation.browser.gesture import note_gesture_sent

    # Once fill is attempted, a confirmation timeout must not type it again.
    note_gesture_sent()
    target.fill(call.value, timeout=timeout)
    _clear_highlight(page)
    _paced_wait(page, PAUSE_AFTER_CLICK_MS, on_frame)
    return f"filled {call.selector} with {call.value!r} (source={call.source})", page_id


def _visible_locator(page: Page, css: str):
    """Prefer a visible match when the alias hits several DOM nodes."""
    loc = page.locator(css)
    n = loc.count()
    for i in range(n):
        el = loc.nth(i)
        try:
            if el.is_visible():
                return el
        except Exception:  # noqa: BLE001
            continue
    return loc.first


def _fill_target_after_point(page: Page, css: str, *, timeout: float):
    """Element that should receive the typed value after a recorded click."""
    focused = page.locator(":focus")
    try:
        if focused.count() > 0:
            tag = (focused.evaluate("e => (e.tagName || '').toLowerCase()") or "")
            if tag in {"input", "textarea", "select"}:
                return focused
    except Exception:  # noqa: BLE001
        pass
    return _visible_locator(page, css)


def navigate(
    page: Page,
    graph: SiteGraph,
    page_id: str,
    call: Navigate,
    *,
    on_frame: Callable[[], None] | None = None,
    mouse_path: list[dict[str, int]] | None = None,
) -> tuple[str, str]:
    url = graph.url_for(call.page_id)
    from navigator.automation.login_match import same_page_path

    try:
        current = page.url or ""
    except Exception:  # noqa: BLE001
        current = ""
    if current and same_page_path(current, url):
        _note_navigation(page, graph, call.page_id)
        return f"already on {call.page_id}", call.page_id
    page.goto(
        url,
        timeout=_action_timeout(call.expects.timeout_ms, navigate=True),
        wait_until="domcontentloaded",
    )
    try:
        from navigator.automation.browser.cursor import install_cursor, _paced_wait

        install_cursor(page)
        if on_frame is not None:
            _paced_wait(page, 600, on_frame)
    except Exception:  # noqa: BLE001
        if on_frame is not None:
            on_frame()
    _note_navigation(page, graph, call.page_id)
    return f"navigated to {call.page_id} ({url})", call.page_id


def wait_for(
    page: Page,
    graph: SiteGraph,
    page_id: str,
    call: WaitFor,
    *,
    on_frame: Callable[[], None] | None = None,
    mouse_path: list[dict[str, int]] | None = None,
) -> tuple[str, str]:
    alias = (call.selector or "").strip().lower()
    if alias == "body":
        return "body ready", page_id
    css = graph.selector(page_id, call.selector)
    if css.strip().lower() == "body":
        return "body ready", page_id
    page.wait_for_selector(
        css, timeout=_action_timeout(call.timeout_ms), state="visible"
    )
    return f"{call.selector} appeared", page_id


def scroll_page(
    page: Page,
    graph: SiteGraph,
    page_id: str,
    call: ScrollPage,
    *,
    on_frame: Callable[[], None] | None = None,
    mouse_path: list[dict[str, int]] | None = None,
) -> tuple[str, str]:
    x = int(call.x or 0)
    y = int(call.y or 0)
    alias = (call.selector or "").strip()
    if alias and alias.lower() != "body":
        css = graph.selector(page_id, alias)
        page.locator(css).first.evaluate(
            "(el, pos) => { el.scrollTo({left: pos.x, top: pos.y}); }",
            {"x": x, "y": y},
            timeout=_action_timeout(2000),
        )
    else:
        page.evaluate(
            "(pos) => { window.scrollTo({left: pos.x, top: pos.y}); }",
            {"x": x, "y": y},
            timeout=_action_timeout(2000),
        )
    try:
        from navigator.automation.browser.cursor import _paced_wait

        _paced_wait(page, 350, on_frame)
    except Exception:  # noqa: BLE001
        if on_frame is not None:
            on_frame()
    where = alias or "window"
    _note_navigation(page, graph, page_id, why="scroll")
    return f"scrolled {where} to ({x},{y})", page_id


def _note_navigation(page: Page, graph: SiteGraph, page_id: str, *, why: str = "navigation") -> None:
    from navigator.automation.browser.viewport_awareness import note_viewport_change

    note_viewport_change(page, graph, page_id, why=why)


_HANDLERS: dict[str, Callable[..., tuple[str, str]]] = {
    "click_element": click_element,
    "fill_field": fill_field,
    "navigate": navigate,
    "wait_for": wait_for,
    "scroll_page": scroll_page,
}
