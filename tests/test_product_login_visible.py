"""Visible vs instant product login fill."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
