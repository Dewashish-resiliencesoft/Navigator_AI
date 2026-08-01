"""Attendee speak + audio hub."""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch
from queue import Queue

from navigator.meeting.attendee import AttendeeClient, _wav_bytes_to_mp3


def test_speak_posts_mp3_output_audio():
    client = AttendeeClient("https://example.test/api/v1", "key")
    captured: dict = {}

    def fake_request(method, path, body=None):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {}

    # Pretend ffmpeg returns mp3 by short-circuiting converter
    with patch(
        "navigator.meeting.attendee._wav_bytes_to_mp3", return_value=b"ID3fake"
    ):
        with patch.object(client, "_request", side_effect=fake_request):
            client.speak("bot_1", b"RIFF....wav")

    assert captured["method"] == "POST"
    assert captured["path"] == "/bots/bot_1/output_audio"
    assert captured["body"]["type"] == "audio/mp3"
    assert base64.b64decode(captured["body"]["data"]) == b"ID3fake"


def test_audio_stream_yields_from_registered_hub():
    client = AttendeeClient("https://example.test/api/v1", "key")
    q: Queue[bytes] = Queue()
    client.register_audio_hub("bot_1", q)
    q.put(b"\x00\x01")
    q.put(b"\x02\x03")
    frames = list(client.audio_stream("bot_1", timeout_s=0.05))
    assert frames == [b"\x00\x01", b"\x02\x03"]


def test_join_includes_websocket_audio_settings():
    client = AttendeeClient("https://example.test/api/v1", "key")
    captured: dict = {}

    def fake_request(method, path, body=None):
        captured["body"] = body
        return {"id": "bot_x", "state": "joining"}

    with patch.object(client, "_request", side_effect=fake_request):
        client.join(
            "https://meet.google.com/abc",
            audio_websocket_url="wss://example.com/ws",
            reserve_voice_agent=True,
        )
    assert captured["body"]["websocket_settings"]["audio"]["url"] == "wss://example.com/ws"


def test_join_google_meet_use_login():
    client = AttendeeClient("https://example.test/api/v1", "key")
    captured: dict = {}

    def fake_request(method, path, body=None):
        captured["body"] = body
        return {"id": "bot_x", "state": "joining"}

    with patch.object(client, "_request", side_effect=fake_request):
        client.join(
            "https://meet.google.com/abc",
            reserve_voice_agent=True,
            google_meet_use_login=True,
        )
    assert captured["body"]["google_meet_settings"]["use_login"] is True
