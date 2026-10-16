"""Action timeouts, one-shot vision, and viewport-only planning. No browser."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as PlaywrightError

from navigator.agent.on_demand_vision import asks_where, look_once, reset_look
from navigator.agent.turn_brain import decide_from_viewport
from navigator.automation.browser import tools
from navigator.automation.browser import viewport_awareness as viewport
from navigator.automation.browser.gesture import note_gesture_sent
from navigator.core.schemas import ClickElement, FillField, Postcondition
from navigator.knowledge.site_graph import SiteGraphError


def _click() -> ClickElement:
    return ClickElement(
        selector="send",
        expects=Postcondition(check="visible", selector="send"),
    )


def test_action_timeout_caps_long_waits_and_keeps_short_ones():
    assert tools._action_timeout(200) == 200
    assert tools._action_timeout(15000) == tools.ACTION_TIMEOUT_CAP_MS
    assert tools._action_timeout(15000, navigate=True) == tools.NAV_TIMEOUT_CAP_MS
    assert tools.ACTION_TIMEOUT_CAP_MS <= 6000
    assert tools.NAV_TIMEOUT_CAP_MS <= 8000


def test_execute_retries_a_timeout_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PlaywrightError("Timeout 6000ms exceeded")
        return "clicked", "home"

    monkeypatch.setitem(tools._HANDLERS, "click_element", handler)
    monkeypatch.setattr(tools.time, "sleep", lambda _s: None)
    result, page_id = tools.execute(SimpleNamespace(), SimpleNamespace(), "home", _click())
    assert calls["n"] == 3
    assert result.ok is True
    assert page_id == "home"
    assert not result.detail.startswith("watchdog")


def test_landed_click_is_not_sent_again_when_confirmation_times_out(monkeypatch):
    """The gesture already landed. A confirmation timeout must not click twice."""
    sent = {"n": 0}

    def handler(*_a, **_k):
        sent["n"] += 1
        note_gesture_sent()
        raise PlaywrightError("Timeout 6000ms exceeded")

    monkeypatch.setitem(tools._HANDLERS, "click_element", handler)
    monkeypatch.setattr(tools.time, "sleep", lambda _s: None)
    result, _page_id = tools.execute(SimpleNamespace(), SimpleNamespace(), "home", _click())
    assert sent["n"] == 1
    assert result.ok is False
    assert "retry exhausted" not in result.detail


def test_landed_fill_is_not_sent_again_when_confirmation_times_out(monkeypatch):
    sent = {"n": 0}

    def handler(*_a, **_k):
        sent["n"] += 1
        note_gesture_sent()
        raise PlaywrightError("Timeout 6000ms exceeded")

    monkeypatch.setitem(tools._HANDLERS, "fill_field", handler)
    monkeypatch.setattr(tools.time, "sleep", lambda _s: None)
    call = FillField(
        selector="email",
        value="a@b.c",
        expects=Postcondition(check="value_equals", selector="email", expected="a@b.c"),
    )
    result, _page_id = tools.execute(SimpleNamespace(), SimpleNamespace(), "home", call)
    assert sent["n"] == 1
    assert result.ok is False


def test_hung_evaluate_is_cancelled_and_the_read_returns(monkeypatch):
    """A page.evaluate that never returns is cancelled inside the hard limit."""
    monkeypatch.setattr(viewport, "EVAL_TIMEOUT_S", 0.2)
    cancelled = {"yes": False}

    async def evaluate(_expression, _arg=None):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled["yes"] = True
            raise

    page = SimpleNamespace(evaluate=evaluate, url="https://example.test/")
    started = time.monotonic()
    awareness = viewport.measure_viewport(page)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    assert cancelled["yes"] is True
    assert awareness.placements == ()


def test_hung_vision_times_out_without_spending_another_sessions_budget(monkeypatch):
    """One session's stuck model call must not block or rate-limit the next."""
    reset_look()
    monkeypatch.setattr(
        "navigator.agent.on_demand_vision.VISION_TIMEOUT_S",
        0.4,
    )
    entered = threading.Event()

    def hung(_system, _user, _png):
        entered.set()
        threading.Event().wait()
        return "too late"

    page = SimpleNamespace(screenshot=lambda **_k: b"\x89PNG\r\n")
    results: dict = {}

    def session_a() -> None:
        results["a"] = look_once(
            page, reason="stuck", complete=hung, session_id="session-a"
        )

    worker = threading.Thread(target=session_a)
    worker.start()
    assert entered.wait(2)
    started = time.monotonic()
    results["b"] = look_once(
        page,
        reason="stuck",
        complete=lambda *_a: "Session B still has a look.",
        session_id="session-b",
    )
    results["b_s"] = time.monotonic() - started
    assert results["b"] == "Session B still has a look."
    assert results["b_s"] < 0.25
    worker.join(2)
    assert not worker.is_alive()
    assert results["a"] == ""
    reset_look()


def test_execute_does_not_retry_a_site_graph_error(monkeypatch):
    calls = {"n": 0}

    def handler(*_a, **_k):
        calls["n"] += 1
        raise SiteGraphError("unknown alias")

    monkeypatch.setitem(tools._HANDLERS, "click_element", handler)
    result, _page_id = tools.execute(SimpleNamespace(), SimpleNamespace(), "home", _click())
    assert calls["n"] == 1
    assert result.ok is False
    assert "unknown alias" in result.detail


def test_execute_marks_a_stuck_step_for_the_watchdog(monkeypatch):
    def handler(*_a, **_k):
        raise PlaywrightError("Timeout 6000ms exceeded")

    monkeypatch.setitem(tools._HANDLERS, "click_element", handler)
    monkeypatch.setattr(tools.time, "sleep", lambda _s: None)
    monkeypatch.setattr(tools, "STEP_WATCHDOG_S", 0)
    result, _page_id = tools.execute(SimpleNamespace(), SimpleNamespace(), "home", _click())
    assert result.ok is False
    assert result.detail.startswith("watchdog:")
    assert "retry exhausted" in result.detail


def test_asks_where_matches_screen_questions_only():
    assert asks_where("where are we")
    assert asks_where("what's on the screen")
    assert asks_where("where do we go next")
    assert not asks_where("show me pricing")
    assert not asks_where("")


def test_look_once_sends_one_image_and_then_rate_limits():
    reset_look()
    page = SimpleNamespace(shots=0)

    def screenshot(**_k):
        page.shots += 1
        return b"\x89PNG\r\n"

    page.screenshot = screenshot
    seen: list[bytes] = []

    def complete(_system, _user, png):
        seen.append(png)
        return "Pricing is visible."

    assert look_once(page, reason="stuck", complete=complete) == "Pricing is visible."
    assert look_once(page, reason="stuck", complete=complete) == ""
    assert page.shots == 1
    assert seen == [b"\x89PNG\r\n"]
    reset_look()


def test_decide_from_viewport_does_not_call_vision_without_a_screenshot():
    def complete_text(system, user):
        assert "do not have a screenshot" in system.lower()
        assert "currently on screen: Pricing" in user
        assert "Blog" not in user
        return (
            '{"intent":"speak","page_id":null,"nav_label":null,'
            '"spoken_response":"This is pricing.","clean_intake":null}'
        )

    def complete_with_image(*_a, **_k):
        raise AssertionError("vision model should not run")

    decision = decide_from_viewport(
        utterance="what is this",
        screen_text="currently on screen: Pricing\nnearby offscreen: FAQ (below, 120px)",
        allowed_pages={"pricing", "home"},
        screenshot_png=b"",
        nav_labels=["Pricing"],
        complete_text=complete_text,
        complete_with_image=complete_with_image,
    )
    assert decision.intent == "speak"
    assert decision.spoken_response == "This is pricing."


def test_on_screen_labels_are_not_the_sitemap():
    from navigator.agent.nodes.planning import _on_screen_labels

    labels = _on_screen_labels(
        "currently on screen: Pricing\n"
        "partly visible: Plans (below, 40px)\n"
        "nearby offscreen: FAQ (below, 120px); Docs (above, 80px)\n"
        "visible: search box, composer"
    )
    assert labels == ["Pricing", "Plans", "FAQ", "Docs", "search box", "composer"]
    assert "Blog" not in labels
    assert all("px" not in label for label in labels)
    assert _on_screen_labels("page inventory: Home, Pricing, Blog") == []
