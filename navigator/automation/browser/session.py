"""Playwright lifecycle.

Headful by default: during a live demo the browser output is what prospects see.
Tests force headless.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from playwright.sync_api import Browser, Page, sync_playwright


@contextmanager
def browser_page(headful: bool = True, slow_mo_ms: int = 0) -> Iterator[Page]:
    """A single Chromium page for the duration of a demo.

    slow_mo_ms paces actions so a human watching a call can follow along; leave it
    at 0 for tests.
    """
    with sync_playwright() as pw:
        browser: Browser = pw.chromium.launch(
            headless=not headful,
            slow_mo=slow_mo_ms,
            args=["--start-maximized"] if headful else [],
        )
        context = browser.new_context(no_viewport=headful)
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()
