"""Click a visible nav label with the demo cursor overlay."""

from __future__ import annotations

from playwright.sync_api import Page

from navigator.automation.browser.cursor import click_with_cursor, install_cursor


def click_nav_label(page: Page, label: str, timeout: float = 8000) -> None:
    text = (label or "").strip()
    if not text:
        raise RuntimeError("empty nav label")
    install_cursor(page)
    # Escape single quotes for :has-text
    safe = text.replace("\\", "\\\\").replace("'", "\\'")
    sel = (
        f"a:has-text('{safe}'), button:has-text('{safe}'), "
        f"[role='link']:has-text('{safe}'), [role='menuitem']:has-text('{safe}')"
    )
    click_with_cursor(page, sel, timeout=timeout)
