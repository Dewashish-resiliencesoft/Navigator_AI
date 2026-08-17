"""Attendee audio hub — Live PCM only, no WAV TTS upload."""

from __future__ import annotations

from unittest.mock import patch
from queue import Queue

from navigator.meeting import attendee as attendee_mod
from navigator.meeting.attendee import AttendeeClient


def test_no_wav_tts_upload():
    assert not hasattr(AttendeeClient, "speak")
    assert not hasattr(attendee_mod, "_wav_bytes_to_mp3")


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
