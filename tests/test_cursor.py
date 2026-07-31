"""Cursor overlay for demos."""

from __future__ import annotations


def test_install_cursor_adds_overlay(page):
    from navigator.browser.cursor import install_cursor

    install_cursor(page)
    assert page.locator("#nav-cursor").count() == 1
    assert page.locator("#nav-cursor-ripple").count() == 1
