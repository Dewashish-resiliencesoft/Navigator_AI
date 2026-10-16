"""Log into a hosted product for live demos."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from navigator.automation.browser.cursor import click_with_cursor, install_cursor

# After submit, success (leave /login/, or the ready element) races a visible
# login error. Wrong credentials must raise within a few seconds. The cap is
# the slow-success budget and stays well under Playwright's 60s default.
_LOGIN_WAIT_MS = 15_000

_LOGIN_OUTCOME_JS = r"""
(ready) => {
  const path = (location.pathname || "").toLowerCase();
  const onLogin = path.includes("/login");
  if (ready) {
    try {
      const hit = document.querySelector(ready);
      if (hit && hit.getClientRects().length) return "ok";
    } catch (e) {}
  }
  if (!ready && !onLogin) return "ok";
  if (!onLogin) return null;
  const bad = /invalid|incorrect|wrong password|login failed|credentials|unauthorized|do not match|try again/i;
  const sel = '[role="alert"], [class*="destructive"], [class*="text-red"], [data-sonner-toast], [class*="error"], [class*="alert"]';
  for (const el of document.querySelectorAll(sel)) {
    if (el.closest && el.closest("#nav-cursor, #nav-cursor-highlight")) continue;
    if (!el.getClientRects().length) continue;
    const cls = typeof el.className === "string" ? el.className : (el.getAttribute("class") || "");
    const text = ((el.innerText || el.textContent || el.getAttribute("aria-label") || "") + "")
      .replace(/\s+/g, " ")
      .trim();
    if (text.length < 2 || text.length > 240) continue;
    const errorEl = el.getAttribute("role") === "alert" || /destructive|alert/i.test(cls);
    if (errorEl || bad.test(text)) return "invalid";
  }
  return null;
}
"""


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

    _await_login_outcome(page, ready_selector)


def _await_login_outcome(page: Page, ready_selector: str | None) -> None:
    """Return once login succeeds, or raise when the form rejects the password."""
    try:
        status = page.wait_for_function(
            _LOGIN_OUTCOME_JS,
            arg=ready_selector or "",
            timeout=_LOGIN_WAIT_MS,
        ).json_value()
    except PlaywrightTimeout:
        raise RuntimeError("invalid credentials") from None
    if status == "invalid":
        raise RuntimeError("invalid credentials")
