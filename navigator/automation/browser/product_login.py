"""Log into a hosted product for live demos."""

from __future__ import annotations

from playwright.sync_api import Page

from navigator.automation.browser.cursor import click_with_cursor, install_cursor


def login_product(
    page: Page,
    *,
    url: str,
    email: str,
    password: str,
    email_selector: str = "#email",
    password_selector: str = "#password",
    submit_selector: str = 'button:has-text("Sign in")',
    ready_selector: str | None = None,
) -> None:
    """Navigate, fill credentials with cursor motion, wait for post-login UI."""
    install_cursor(page)
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(800)

    # Some products splash then route to /login — wait briefly for the form.
    if page.locator(email_selector).count() == 0:
        page.wait_for_timeout(2000)
    if page.locator(email_selector).count() == 0 and "/login" not in page.url:
        from urllib.parse import urljoin

        page.goto(urljoin(url, "/login/"), wait_until="domcontentloaded", timeout=60_000)
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
        # Logged-in apps usually leave /login/
        page.wait_for_function(
            "() => !location.pathname.includes('/login')",
            timeout=60_000,
        )
