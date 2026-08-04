"""The tool interface. A tool never raises; a failure is a ToolResult with ok=False."""

from __future__ import annotations

import time

from navigator.automation.browser.tools import execute
from navigator.core.schemas import ClickElement, FillField, Navigate, Postcondition, WaitFor

VISIBLE_INPUT = Postcondition(check="visible", selector="message_input")


def test_navigate_moves_page_id(page, site_graph):
    call = Navigate(
        page_id="inbox", expects=Postcondition(check="visible", selector="composer")
    )
    result, next_page = execute(page, site_graph, "inbox", call)
    assert result.ok
    assert next_page == "inbox"
    assert "crm_dashboard.html" in result.detail


def test_fill_field_writes_value(page, site_graph):
    call = FillField(
        selector="message_input",
        value="hello there",
        expects=Postcondition(
            check="value_equals", selector="message_input", expected="hello there"
        ),
    )
    result, _ = execute(page, site_graph, "inbox", call)
    assert result.ok
    assert page.input_value("#message-input") == "hello there"


def test_fill_field_records_user_source(page, site_graph):
    """Live prospect-supplied data must be distinguishable in the log."""
    call = FillField(
        selector="message_input",
        value="my own data",
        source="user",
        expects=Postcondition(
            check="value_equals", selector="message_input", expected="my own data"
        ),
    )
    result, _ = execute(page, site_graph, "inbox", call)
    assert result.ok
    assert "source=user" in result.detail


def test_click_element_fires_handler(page, site_graph):
    page.fill("#message-input", "sent via click")
    call = ClickElement(
        selector="send_button",
        expects=Postcondition(check="visible", selector="sent_bubble"),
    )
    result, _ = execute(page, site_graph, "inbox", call)
    assert result.ok
    assert page.inner_text(".message.sent") == "sent via click"


def test_wait_for_returns_when_present(page, site_graph):
    call = WaitFor(selector="send_button", timeout_ms=2000, expects=VISIBLE_INPUT)
    result, _ = execute(page, site_graph, "inbox", call)
    assert result.ok


# --- failure paths -----------------------------------------------------------


def test_wait_for_times_out_without_raising(page, site_graph):
    """Postcondition timeout behaviour: bounded wait, ok=False, no exception."""
    page.evaluate("document.querySelector('#send-btn').remove()")
    call = WaitFor(selector="send_button", timeout_ms=600, expects=VISIBLE_INPUT)

    started = time.perf_counter()
    result, next_page = execute(page, site_graph, "inbox", call)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert result.ok is False
    assert result.tool == "wait_for"
    assert "Timeout" in result.detail
    assert 480 <= elapsed_ms < 4000, "must honour its own timeout_ms, not the default"
    assert result.duration_ms >= 480
    assert next_page == "inbox", "a failed call must not move the agent"


def test_click_timeout_uses_postcondition_timeout(page, site_graph):
    page.evaluate("document.querySelector('#send-btn').remove()")
    call = ClickElement(
        selector="send_button",
        expects=Postcondition(check="visible", selector="sent_bubble", timeout_ms=500),
    )
    started = time.perf_counter()
    result, _ = execute(page, site_graph, "inbox", call)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert result.ok is False
    assert 400 <= elapsed_ms < 4000


def test_unknown_selector_alias_is_a_result_not_an_exception(page, site_graph):
    """A site graph bug must be logged like any other failure, not crash the call."""
    call = ClickElement(selector="ghost_button", expects=VISIBLE_INPUT)
    result, _ = execute(page, site_graph, "inbox", call)
    assert result.ok is False
    assert "no selector 'ghost_button'" in result.detail


def test_fill_on_missing_element_fails_cleanly(page, site_graph):
    page.evaluate("document.querySelector('#message-input').remove()")
    call = FillField(
        selector="message_input",
        value="x",
        expects=Postcondition(
            check="value_equals", selector="message_input", expected="x", timeout_ms=500
        ),
    )
    result, _ = execute(page, site_graph, "inbox", call)
    assert result.ok is False
    assert result.duration_ms >= 500
