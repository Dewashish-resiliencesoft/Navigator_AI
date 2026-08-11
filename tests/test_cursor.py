"""Cursor overlay for demos."""

from __future__ import annotations


def test_install_cursor_adds_overlay(page):
    from navigator.automation.browser.cursor import install_cursor

    install_cursor(page)
    assert page.locator("#nav-cursor").count() == 1
    assert page.locator("#nav-cursor-ripple").count() == 1
    assert page.locator("#nav-cursor-highlight").count() == 1


def test_move_duration_ms_scales_with_distance():
    from navigator.automation.browser.cursor import (
        MOVE_MS_LONG,
        MOVE_MS_SHORT,
        move_duration_ms,
    )

    assert move_duration_ms(0) == MOVE_MS_SHORT
    assert move_duration_ms(180) == MOVE_MS_SHORT
    assert move_duration_ms(600) == MOVE_MS_LONG
    mid = move_duration_ms(390)
    assert MOVE_MS_SHORT < mid < MOVE_MS_LONG


def test_click_with_cursor_fires_real_click(page, monkeypatch):
    from navigator.automation.browser import cursor as cursor_mod
    from navigator.automation.browser.cursor import click_with_cursor

    monkeypatch.setattr(cursor_mod, "MOTION_SCALE", 0.0)
    page.set_content('<button id="b">Go</button>')
    page.evaluate(
        "document.getElementById('b').onclick = () => { window.__navClicks = (window.__navClicks||0)+1 }"
    )
    click_with_cursor(page, "#b")
    assert page.evaluate("window.__navClicks") >= 1
    assert page.locator("#nav-cursor").count() == 1


def test_click_with_cursor_supports_has_text(page, monkeypatch):
    """Login submit uses :has-text — must not blow up in document.querySelector."""
    from navigator.automation.browser import cursor as cursor_mod
    from navigator.automation.browser.cursor import click_with_cursor

    monkeypatch.setattr(cursor_mod, "MOTION_SCALE", 0.0)
    page.set_content('<button type="button">Sign in</button>')
    page.evaluate(
        "document.querySelector('button').onclick = () => { window.__navClicks = 1 }"
    )
    click_with_cursor(page, 'button:has-text("Sign in")')
    assert page.evaluate("window.__navClicks") == 1


def test_move_on_frame_called_many_times(page, monkeypatch):
    from navigator.automation.browser import cursor as cursor_mod
    from navigator.automation.browser.cursor import install_cursor, move_cursor

    monkeypatch.setattr(cursor_mod, "MOTION_SCALE", 1.0)
    monkeypatch.setattr(cursor_mod, "FRAME_STEPS", 12)
    page.set_content("<body></body>")
    page.wait_for_timeout = lambda _ms: None  # type: ignore[method-assign]
    install_cursor(page)
    hits: list[int] = []

    move_cursor(page, 120, 80, on_frame=lambda: hits.append(1))
    assert len(hits) >= 10


def test_click_with_cursor_uses_recorded_path_coords(page, monkeypatch):
    """With mouse_path, click the recorded point — not a CSS scroll-jump."""
    from navigator.automation.browser import cursor as cursor_mod
    from navigator.automation.browser.cursor import click_with_cursor

    monkeypatch.setattr(cursor_mod, "MOTION_SCALE", 0.0)
    monkeypatch.setattr(cursor_mod, "_playback_mode", True)
    page.set_content(
        '<div style="height:2000px"></div>'
        '<button id="b" style="position:fixed;left:100px;top:80px;width:40px;height:24px">Go</button>'
    )
    page.evaluate(
        """() => {
          window.__navClicks = 0;
          window.__navClickXY = null;
          document.getElementById('b').addEventListener('click', (ev) => {
            window.__navClicks += 1;
            window.__navClickXY = {x: ev.clientX, y: ev.clientY};
          });
        }"""
    )
    path = [
        {"x": 20, "y": 20, "at_ms": 0},
        {"x": 60, "y": 50, "at_ms": 40},
        {"x": 110, "y": 90, "at_ms": 80},
    ]
    # Wrong selector on purpose — path coords must still hit the button.
    click_with_cursor(page, "#missing", mouse_path=path, timeout=1000)
    assert page.evaluate("window.__navClicks") >= 1
    xy = page.evaluate("window.__navClickXY")
    assert abs(xy["x"] - 110) <= 2
    assert abs(xy["y"] - 90) <= 2


def test_playback_mode_scales_durations(monkeypatch):
    """Timeline replay must actually speed motion — not only cut frame hops."""
    from navigator.automation.browser import cursor as cursor_mod

    monkeypatch.setattr(cursor_mod, "MOTION_SCALE", 1.0)
    cursor_mod.set_playback_mode(False)
    normal = cursor_mod._scaled_ms(1000)
    cursor_mod.set_playback_mode(True)
    try:
        fast = cursor_mod._scaled_ms(1000)
        assert fast < normal
        assert abs(fast - 220.0) < 0.01
    finally:
        cursor_mod.set_playback_mode(False)

