"""Gated product login with one retry and a fail-loud apology path."""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Protocol


LOGIN_RETRY_LINE = "Give me one moment — I'm signing back into the product."

LOGIN_APOLOGY = (
    "Sorry about that — there seems to be a server-side issue on our end. "
    "We apologize for the inconvenience. Please try again in a little while "
    "while we sort this out."
)


class LoginGateResult(Enum):
    skipped = "skipped"
    ok = "ok"
    failed = "failed"


class _Speaker(Protocol):
    def say(self, text: str) -> None: ...


class _Attendee(Protocol):
    def send_chat(self, bot_id: str, message: str, *, to: str = "everyone") -> None: ...


def _say(
    speaker: _Speaker | None,
    attendee: _Attendee | None,
    bot_id: str | None,
    text: str,
) -> None:
    if speaker is not None:
        speaker.say(text)
    if attendee is not None and bot_id:
        try:
            attendee.send_chat(bot_id, text)
        except Exception as exc:  # noqa: BLE001
            print(f"[live] login_gate chat failed: {exc}", flush=True)


def run_login_gate(
    *,
    login_fn: Callable[..., None],
    url: str,
    email: str,
    password: str,
    speaker: _Speaker | None = None,
    attendee: _Attendee | None = None,
    bot_id: str | None = None,
    login_kwargs: dict[str, Any] | None = None,
) -> LoginGateResult:
    """Run product login with at most one retry. Empty creds → skipped."""
    if not (email.strip() and password.strip()):
        print("[live] login=skip (no creds)", flush=True)
        return LoginGateResult.skipped

    kwargs = {"url": url, "email": email, "password": password, **(login_kwargs or {})}
    try:
        login_fn(**kwargs)
        print("[live] login=pass attempt=1", flush=True)
        return LoginGateResult.ok
    except Exception as first:  # noqa: BLE001
        print(f"[live] login=fail attempt=1 err={first!r}", flush=True)
        _say(speaker, attendee, bot_id, LOGIN_RETRY_LINE)
        try:
            login_fn(**kwargs)
            print("[live] login=pass attempt=2", flush=True)
            return LoginGateResult.ok
        except Exception as second:  # noqa: BLE001
            print(f"[live] login=fail attempt=2 err={second!r}", flush=True)
            _say(speaker, attendee, bot_id, LOGIN_APOLOGY)
            return LoginGateResult.failed
