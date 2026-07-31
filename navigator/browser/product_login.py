"""Log into a hosted product for live demos."""

from __future__ import annotations

from playwright.sync_api import Page

from navigator.browser.cursor import click_with_cursor, install_cursor


def login_product(
    page: Page,
    *,
    url: str,
    email: str,
    password: str,
    email_selector: str = (
        'input[type="email"], input[name="email"], input[name="username"], #email'
    ),
    password_selector: str = (
        'input[type="password"], input[name="password"], #password'
    ),
    submit_selector: str = (
        'button[type="submit"], button:has-text("Log in"), '
        'button:has-text("Login"), button:has-text("Sign in")'
    ),
    ready_selector: str | None = None,
) -> None:
    """Navigate, fill credentials with cursor motion, wait for post-login UI."""
    install_cursor(page)
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(800)

    if ready_selector and page.locator(ready_selector).count() > 0:
        return

    email_loc = page.locator(email_selector).first
    email_loc.wait_for(state="visible", timeout=30_000)
    email_loc.fill(email, timeout=15_000)
    page.locator(password_selector).first.fill(password, timeout=15_000)
    click_with_cursor(page, submit_selector, timeout=15_000)

    if ready_selector:
        page.wait_for_selector(ready_selector, timeout=60_000)
    else:
        page.wait_for_load_state("networkidle", timeout=60_000)
