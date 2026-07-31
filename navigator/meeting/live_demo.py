"""Orchestrate: Teams notify → Playwright login → relay → tunnel → Attendee join."""

from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from navigator.browser.cursor import install_cursor, move_cursor
from navigator.browser.product_login import login_product
from navigator.meeting.attendee import AttendeeClient
from navigator.meeting.relay import push_frame, start_relay
from navigator.meeting.teams import notify_demo_link
from navigator.meeting.tunnel import start_tunnel
from navigator.settings import settings


def _require_live_settings() -> None:
    if "localhost" in settings.attendee_base_url:
        raise RuntimeError(
            "NAVIGATOR_ATTENDEE_BASE_URL still points at localhost; "
            "use https://app.attendee.dev/api/v1 (or your self-hosted host)"
        )
    missing = [
        name
        for name, val in [
            ("NAVIGATOR_ATTENDEE_API_KEY", settings.attendee_api_key),
            ("NAVIGATOR_MEETING_URL", settings.meeting_url),
            ("NAVIGATOR_PRODUCT_URL", settings.product_url),
            ("NAVIGATOR_PRODUCT_LOGIN_EMAIL", settings.product_login_email),
            ("NAVIGATOR_PRODUCT_LOGIN_PASSWORD", settings.product_login_password),
            ("NAVIGATOR_TEAMS_WEBHOOK_URL", settings.teams_webhook_url),
        ]
        if not val
    ]
    if missing:
        raise RuntimeError(f"missing env for live Meet demo: {', '.join(missing)}")


def wait_until_joined(
    client: AttendeeClient, bot_id: str, *, timeout_s: float = 120.0
) -> None:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        bot = client.get(bot_id)
        last = bot.raw_state or bot.state
        if bot.state == "joined":
            return
        if bot.state == "fatal_error":
            raise RuntimeError(f"Attendee bot fatal_error (last state={last})")
        time.sleep(2)
    raise TimeoutError(f"Attendee bot did not join within {timeout_s}s (last={last})")


def run_live_meet_smoke(
    *,
    hold_s: float = 30.0,
    headful: bool = True,
    fail_screenshot: Path | None = None,
) -> str:
    """Join Meet streaming the Playwright product session. Returns bot id."""
    _require_live_settings()

    notify_demo_link(
        webhook_url=settings.teams_webhook_url,
        meeting_url=settings.meeting_url,
    )

    client = AttendeeClient(settings.attendee_base_url, settings.attendee_api_key)
    relay = start_relay()
    tunnel = None
    bot_id: str | None = None

    try:
        tunnel = start_tunnel(relay.port, binary=settings.tunnel_bin)
        public_view = f"{tunnel.public_url}/view"

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headful)
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            page = context.new_page()
            install_cursor(page)
            try:
                login_product(
                    page,
                    url=settings.product_url,
                    email=settings.product_login_email,
                    password=settings.product_login_password,
                )
            except Exception:
                path = fail_screenshot or Path("/tmp/nav-login-fail.png")
                page.screenshot(path=str(path))
                raise RuntimeError(f"product login failed; screenshot at {path}") from None

            push_frame(relay, page)
            bot = client.join(
                settings.meeting_url,
                bot_name="Navigator AI",
                voice_agent_url=public_view,
            )
            bot_id = bot.id
            wait_until_joined(client, bot.id)

            deadline = time.time() + hold_s
            while time.time() < deadline:
                push_frame(relay, page)
                # gentle cursor wander so Meet viewers see motion
                move_cursor(page, 200 + (time.time() % 5) * 80, 180 + (time.time() % 3) * 40)
                time.sleep(0.25)

            context.close()
            browser.close()
    finally:
        if bot_id is not None:
            try:
                client.leave(bot_id)
            except Exception:
                pass
        if tunnel is not None:
            tunnel.stop()
        relay.stop()

    return bot_id or ""


if __name__ == "__main__":
    print(run_live_meet_smoke())
