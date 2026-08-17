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


def test_screencast_playback_does_not_crush_motion_scale(monkeypatch):
    """Meet sees CSS animation — playback 0.22x would look like a teleport."""
    from navigator.automation.browser import cursor as cursor_mod

    monkeypatch.setattr(cursor_mod, "MOTION_SCALE", 1.0)
    cursor_mod.set_playback_mode(True)
    cursor_mod.set_screencast_mode(True)
    try:
        assert abs(cursor_mod._motion_scale() - 1.0) < 0.01
    finally:
        cursor_mod.set_screencast_mode(False)
        cursor_mod.set_playback_mode(False)


def test_trail_playback_uses_approach_not_full_recording(monkeypatch):
    """A 46s recorded wander must replay as a ~1s glide to the click, not 350ms."""
    from navigator.automation.browser import cursor as cursor_mod
    from navigator.automation.browser.cursor import (
        TRAIL_MAX_FRAMES,
        TRAIL_MAX_MS,
        TRAIL_MIN_MS,
        _trail_playback,
    )

    monkeypatch.setattr(cursor_mod, "MOTION_SCALE", 1.0)

    # ~46s of host wandering then a click — same shape as onboarding step 1.
    points = [
        {"x": 100 + i, "y": 50 + (i % 7), "at_ms": i * 80} for i in range(576)
    ]
    frames, dur = _trail_playback(points)
    assert TRAIL_MIN_MS <= dur <= TRAIL_MAX_MS
    assert 2 <= len(frames) <= TRAIL_MAX_FRAMES
    assert frames[0]["offset"] == 0.0
    assert frames[-1]["offset"] == 1.0
    assert frames[-1]["x"] == points[-1]["x"]
    assert frames[-1]["y"] == points[-1]["y"]
    # Approach only — first keyframe is near the end, not the start of the wander.
    assert frames[0]["x"] > points[0]["x"] + 200


def test_trail_playback_short_path_keeps_start(monkeypatch):
    from navigator.automation.browser import cursor as cursor_mod
    from navigator.automation.browser.cursor import _trail_playback

    monkeypatch.setattr(cursor_mod, "MOTION_SCALE", 1.0)

    points = [
        {"x": 10, "y": 10, "at_ms": 0},
        {"x": 40, "y": 20, "at_ms": 200},
        {"x": 80, "y": 40, "at_ms": 500},
    ]
    frames, dur = _trail_playback(points)
    assert dur >= 700
    assert frames[0]["x"] == 10
    assert frames[-1]["x"] == 80


def test_click_with_cursor_scrolls_below_fold_target(page, monkeypatch):
    """Recorded path is viewport coords; demo must scroll so Meet sees the target."""
    from navigator.automation.browser import cursor as cursor_mod
    from navigator.automation.browser.cursor import click_with_cursor

    monkeypatch.setattr(cursor_mod, "MOTION_SCALE", 0.0)
    page.set_viewport_size({"width": 800, "height": 400})
    page.set_content(
        '<div style="height:1600px"></div>'
        '<button id="deep" style="width:80px;height:24px">Send campaign</button>'
    )
    page.evaluate(
        """() => {
          window.__navClicks = 0;
          document.getElementById('deep').addEventListener('click', () => {
            window.__navClicks += 1;
          });
        }"""
    )
    path = [
        {"x": 20, "y": 20, "at_ms": 0},
        {"x": 40, "y": 40, "at_ms": 40},
    ]
    assert page.evaluate("window.scrollY") == 0
    click_with_cursor(page, "#deep", mouse_path=path, timeout=3000)
    assert page.evaluate("window.__navClicks") >= 1
    assert page.evaluate("window.scrollY") > 200

