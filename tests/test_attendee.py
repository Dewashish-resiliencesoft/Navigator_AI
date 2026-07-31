"""Attendee REST client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from navigator.meeting.attendee import AttendeeClient


def _resp(body: bytes, status: int = 200) -> MagicMock:
    fake = MagicMock()
    fake.status = status
    fake.read.return_value = body
    fake.__enter__.return_value = fake
    fake.__exit__.return_value = False
    return fake


def test_join_posts_bot_and_voice_agent_url():
    client = AttendeeClient("https://app.attendee.dev/api/v1", "tok")
    captured: dict = {}

    real_request = __import__("urllib.request", fromlist=["Request"]).Request

    def capture_request(*args, **kwargs):
        req = real_request(*args, **kwargs)
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["auth"] = req.get_header("Authorization")
        return req

    with patch("navigator.meeting.attendee.Request", side_effect=capture_request):
        with patch(
            "navigator.meeting.attendee.urlopen",
            return_value=_resp(b'{"id":"bot_1","state":"joining"}', 201),
        ):
            bot = client.join(
                "https://meet.google.com/x",
                bot_name="Navigator AI",
                voice_agent_url="https://tunnel.example/view",
            )
    assert bot.id == "bot_1"
    assert bot.state == "joining"
    body = json.loads(captured["data"])
    assert body["meeting_url"] == "https://meet.google.com/x"
    assert body["voice_agent_settings"]["url"] == "https://tunnel.example/view"
    assert captured["auth"] == "Token tok"


def test_get_maps_joined_recording():
    client = AttendeeClient("https://app.attendee.dev/api/v1", "tok")
    with patch("navigator.meeting.attendee.Request"):
        with patch(
            "navigator.meeting.attendee.urlopen",
            return_value=_resp(b'{"id":"bot_1","state":"joined_recording"}'),
        ):
            bot = client.get("bot_1")
    assert bot.state == "joined"
    assert bot.raw_state == "joined_recording"


def test_leave_posts():
    client = AttendeeClient("https://app.attendee.dev/api/v1", "tok")
    with patch("navigator.meeting.attendee.Request"):
        with patch(
            "navigator.meeting.attendee.urlopen",
            return_value=_resp(b"{}"),
        ):
            client.leave("bot_1")
