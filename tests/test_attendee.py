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


def test_join_voice_agents_disabled_hint():
    from urllib.error import HTTPError

    client = AttendeeClient("https://app.attendee.dev/api/v1", "tok")
    err = HTTPError(
        url="https://x/bots",
        code=400,
        msg="Bad Request",
        hdrs=None,
        fp=MagicMock(read=MagicMock(return_value=b'{"voice_agent_settings":["Voice agents are not enabled"]}')),
    )
    with patch("navigator.meeting.attendee.urlopen", side_effect=err):
        try:
            client.join("https://meet.google.com/abc-defg-hij")
        except RuntimeError as exc:
            assert "ENABLE_VOICE_AGENTS" in str(exc) or "voice agents" in str(exc).lower()
        else:
            raise AssertionError("expected RuntimeError")


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
            # Even if caller passes voice_agent_url, Attendee only gets screenshare_url.
            client.enable_screenshare(
                "bot_1",
                "https://tunnel.example/view",
                voice_agent_url="https://tunnel.example/agent",
            )
    assert captured["method"] == "PATCH"
    assert captured["url"].endswith("/bots/bot_1/voice_agent_settings")
    assert json.loads(captured["data"]) == {
        "screenshare_url": "https://tunnel.example/view"
    }


def test_set_voice_agent_url_patches_url_only():
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
            return_value=_resp(b"{}"),
        ):
            client.set_voice_agent_url("bot_1", "https://tunnel.example/agent")
    assert json.loads(captured["data"]) == {"url": "https://tunnel.example/agent"}


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


def test_human_has_left_tracks_join_then_leave():
    from navigator.meeting.attendee import ParticipantEvent

    client = AttendeeClient("https://app.attendee.dev/api/v1", "tok")
    joined = [
        ParticipantEvent("Dewa", "join"),
        ParticipantEvent("Navigator AI", "join"),
    ]
    left = joined + [ParticipantEvent("Dewa", "leave")]
    with patch.object(client, "participant_events", return_value=joined):
        assert (
            client.human_has_left(
                "bot_1",
                human_name="Dewa",
                bot_names=frozenset({"Navigator AI"}),
            )
            is False
        )
    with patch.object(client, "participant_events", return_value=left):
        assert (
            client.human_has_left(
                "bot_1",
                human_name="Dewa",
                bot_names=frozenset({"Navigator AI"}),
            )
            is True
        )
