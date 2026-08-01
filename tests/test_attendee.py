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


def test_join_reserves_resources_without_screenshare():
    client = AttendeeClient("https://app.attendee.dev/api/v1", "tok")
    captured: dict = {}
    real_request = __import__("urllib.request", fromlist=["Request"]).Request

    def capture_request(*args, **kwargs):
        req = real_request(*args, **kwargs)
        captured["data"] = req.data
        return req

    with patch("navigator.meeting.attendee.Request", side_effect=capture_request):
        with patch(
            "navigator.meeting.attendee.urlopen",
            return_value=_resp(b'{"id":"bot_1","state":"joining"}', 201),
        ):
            bot = client.join(
                "https://meet.google.com/x",
                reserve_voice_agent=True,
                join_chat_message="Waiting for you",
            )
    assert bot.id == "bot_1"
    body = json.loads(captured["data"])
    assert body["voice_agent_settings"] == {"reserve_resources": True}
    assert body["bot_chat_message"]["message"] == "Waiting for you"


def test_join_includes_zoom_tokens_url_for_host_zak():
    client = AttendeeClient("https://app.attendee.dev/api/v1", "tok")
    captured: dict = {}
    real_request = __import__("urllib.request", fromlist=["Request"]).Request

    def capture_request(*args, **kwargs):
        req = real_request(*args, **kwargs)
        captured["data"] = req.data
        return req

    with patch("navigator.meeting.attendee.Request", side_effect=capture_request):
        with patch(
            "navigator.meeting.attendee.urlopen",
            return_value=_resp(b'{"id":"bot_z","state":"joining"}', 201),
        ):
            client.join(
                "https://zoom.us/j/1",
                reserve_voice_agent=True,
                zoom_tokens_url="https://api.example/v1/zoom/zak?secret=s",
            )
    body = json.loads(captured["data"])
    assert body["callback_settings"]["zoom_tokens_url"] == (
        "https://api.example/v1/zoom/zak?secret=s"
    )
    assert "zoom_settings" not in body


def test_join_zoom_voice_agent_can_request_web_sdk():
    client = AttendeeClient("https://app.attendee.dev/api/v1", "tok")
    captured: dict = {}
    real_request = __import__("urllib.request", fromlist=["Request"]).Request

    def capture_request(*args, **kwargs):
        req = real_request(*args, **kwargs)
        captured["data"] = req.data
        return req

    with patch("navigator.meeting.attendee.Request", side_effect=capture_request):
        with patch(
            "navigator.meeting.attendee.urlopen",
            return_value=_resp(b'{"id":"bot_z","state":"joining"}', 201),
        ):
            client.join(
                "https://zoom.us/j/1",
                reserve_voice_agent=True,
                zoom_tokens_url="https://api.example/v1/zoom/zak",
                zoom_sdk="web",
            )
    body = json.loads(captured["data"])
    assert body["zoom_settings"]["sdk"] == "web"


def test_enable_screenshare_patches():
    client = AttendeeClient("https://app.attendee.dev/api/v1", "tok")
    captured: dict = {}
    real_request = __import__("urllib.request", fromlist=["Request"]).Request

    def capture_request(*args, **kwargs):
        req = real_request(*args, **kwargs)
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["data"] = req.data
        return req

    with patch("navigator.meeting.attendee.Request", side_effect=capture_request):
        with patch(
            "navigator.meeting.attendee.urlopen",
            return_value=_resp(b"{}"),
        ):
            client.enable_screenshare("bot_1", "https://tunnel.example/view")
    assert captured["method"] == "PATCH"
    assert captured["url"].endswith("/bots/bot_1/voice_agent_settings")
    assert json.loads(captured["data"]) == {
        "screenshare_url": "https://tunnel.example/view"
    }


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
