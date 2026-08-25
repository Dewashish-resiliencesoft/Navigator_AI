"""Log into a hosted product for live demos."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import Page

from navigator.automation.browser.cursor import click_with_cursor, install_cursor


def open_login_page(
    page: Page,
    *,
    url: str,
    email_selector: str = "#email",
) -> None:
    """Navigate to the login form without submitting credentials."""
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


def login_product(
    page: Page,
    *,
    url: str,
    email: str,
    password: str,
    email_selector: str = "#email",
    password_selector: str = "#password",
    # Prefer labeled Sign-in — carousel sites often have other type=submit buttons.
    submit_selector: str = 'button:has-text("Sign in")',
    ready_selector: str | None = None,
    visible: bool = False,
    skip_open: bool = False,
    on_progress: Callable[[], None] | None = None,
) -> None:
    """Navigate, fill credentials with cursor motion, wait for post-login UI.

    ``visible=True`` types email on screen (for live screenshare demos) instead of
    instant ``fill``. ``skip_open=True`` when the page is already on the login form.
    """
    if not skip_open:
        open_login_page(page, url=url, email_selector=email_selector)

    if ready_selector and page.locator(ready_selector).count() > 0:
        return

    email_loc = page.locator(email_selector).first
    email_loc.wait_for(state="visible", timeout=30_000)

    if visible:
        click_with_cursor(page, email_selector)
        email_loc.fill("")
        email_loc.press_sequentially(email, delay=75)
        if on_progress:
            on_progress()
        page.wait_for_timeout(400)
        click_with_cursor(page, password_selector)
        page.locator(password_selector).first.fill(password, timeout=15_000)
        if on_progress:
            on_progress()
        page.wait_for_timeout(250)
        click_with_cursor(page, submit_selector, timeout=15_000)
    else:
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
