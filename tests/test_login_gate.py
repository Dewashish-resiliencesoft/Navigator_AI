from __future__ import annotations

from navigator.automation.browser.login_gate import (
    LOGIN_APOLOGY,
    LOGIN_RETRY_LINE,
    LoginGateResult,
    run_login_gate,
)


class FakeSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)


class FakeAttendee:
    def __init__(self) -> None:
        self.chats: list[str] = []

    def send_chat(self, bot_id: str, message: str, *, to: str = "everyone") -> None:
        self.chats.append(message)


def test_login_gate_skips_when_no_creds():
    calls: list[str] = []

    def login(**kwargs):
        calls.append("login")

    result = run_login_gate(
        login_fn=login,
        url="https://example.com",
        email="",
        password="",
        speaker=FakeSpeaker(),
        attendee=None,
        bot_id=None,
    )
    assert result == LoginGateResult.skipped
    assert calls == []


def test_login_gate_passes_first_try():
    def login(**kwargs):
        return None

    speaker = FakeSpeaker()
    result = run_login_gate(
        login_fn=login,
        url="https://example.com",
        email="a@b.com",
        password="secret",
        speaker=speaker,
        attendee=None,
        bot_id=None,
    )
    assert result == LoginGateResult.ok
    assert speaker.said == []


def test_login_gate_retries_then_ok():
    attempts = {"n": 0}

    def login(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("still on /login")

    speaker = FakeSpeaker()
    attendee = FakeAttendee()
    result = run_login_gate(
        login_fn=login,
        url="https://example.com",
        email="a@b.com",
        password="secret",
        speaker=speaker,
        attendee=attendee,
        bot_id="bot1",
    )
    assert result == LoginGateResult.ok
    assert attempts["n"] == 2
    assert LOGIN_RETRY_LINE in speaker.said[0]
    # Voice only — never mirror into Meet chat.
    assert attendee.chats == []


def test_login_gate_double_fail_apology():
    def login(**kwargs):
        raise RuntimeError("login failed")

    speaker = FakeSpeaker()
    attendee = FakeAttendee()
    result = run_login_gate(
        login_fn=login,
        url="https://example.com",
        email="a@b.com",
        password="secret",
        speaker=speaker,
        attendee=attendee,
        bot_id="bot1",
    )
    assert result == LoginGateResult.failed
    assert any(LOGIN_RETRY_LINE in s for s in speaker.said)
    assert any(LOGIN_APOLOGY in s for s in speaker.said)
    assert attendee.chats == []
