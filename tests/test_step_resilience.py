"""Action timeouts, one-shot vision, and viewport-only planning. No browser."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as PlaywrightError

from navigator.agent.on_demand_vision import asks_where, look_once, reset_look
from navigator.agent.turn_brain import decide_from_viewport
from navigator.automation.browser import tools
from navigator.core.schemas import ClickElement, Postcondition
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
        "currently on screen: Pricing\nnearby offscreen: FAQ (below, 120px)"
    )
    assert labels == ["Pricing", "FAQ (below, 120px)"]
    assert "Blog" not in labels
    assert _on_screen_labels("page inventory: Home, Pricing, Blog") == []
