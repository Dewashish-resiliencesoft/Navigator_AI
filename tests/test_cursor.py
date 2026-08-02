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
