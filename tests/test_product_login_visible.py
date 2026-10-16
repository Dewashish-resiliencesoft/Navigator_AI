"""Visible vs instant product login fill."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from navigator.automation.browser import product_login


def test_login_product_visible_types_email():
    page = MagicMock()
    email_loc = MagicMock()
    pwd_loc = MagicMock()

    def locator_side(sel):
        m = MagicMock()
        m.first = email_loc if "email" in sel else pwd_loc
        m.count.return_value = 1
        return m

    page.locator.side_effect = locator_side
    email_loc.wait_for = MagicMock()

    with patch.object(product_login, "open_login_page"), patch.object(
        product_login, "click_with_cursor"
    ):
        product_login.login_product(
            page,
            url="https://app.test/login",
            email="demo@test.com",
            password="secret",
            visible=True,
            skip_open=True,
        )

    email_loc.fill.assert_any_call("")
    email_loc.press_sequentially.assert_called_once_with("demo@test.com", delay=75)
    pwd_loc.fill.assert_called_once_with("secret", timeout=15_000)


def test_login_product_fast_fill_when_not_visible():
    page = MagicMock()
    email_loc = MagicMock()
    pwd_loc = MagicMock()

    def locator_side(sel):
        m = MagicMock()
        m.first = email_loc if "email" in sel else pwd_loc
        m.count.return_value = 0
        return m

    page.locator.side_effect = locator_side
    email_loc.wait_for = MagicMock()

    with patch.object(product_login, "open_login_page"), patch.object(
        product_login, "click_with_cursor"
    ):
        product_login.login_product(
            page,
            url="https://app.test/login",
            email="demo@test.com",
            password="secret",
            visible=False,
        )

    email_loc.fill.assert_called_once_with("demo@test.com", timeout=15_000)
    email_loc.press_sequentially.assert_not_called()


_BAD_PASSWORD_HTML = """<!doctype html>
<html><body>
<form>
  <input id="email" />
  <input id="password" type="password" />
  <button type="submit">Sign in</button>
  <div id="banner"></div>
</form>
<script>
  document.querySelector("form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    window.__submitted = (window.__submitted || 0) + 1;
    const banner = document.getElementById("banner");
    banner.className = "mb-5 border-destructive/40 bg-destructive/10 text-destructive";
    banner.textContent = "Login failed";
  });
</script>
</body></html>
"""


def test_bad_password_fails_fast_with_invalid_credentials(browser, monkeypatch):
    """A rejected password must not sit on the 60s leave-/login/ wait."""
    from navigator.automation.browser import cursor as cursor_mod

    monkeypatch.setattr(cursor_mod, "MOTION_SCALE", 0.0)
    context = browser.new_context()
    page = context.new_page()
    try:
        page.route(
            "https://app.test/**",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=_BAD_PASSWORD_HTML,
            ),
        )
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="invalid credentials"):
            product_login.login_product(
                page,
                url="https://app.test/login/",
                email="demo@test.com",
                password="wrong-password",
                visible=False,
            )
        elapsed = time.monotonic() - started
        assert page.evaluate("window.__submitted") == 1
        assert "/login" in page.url
        assert elapsed < 5.0
    finally:
        context.close()
