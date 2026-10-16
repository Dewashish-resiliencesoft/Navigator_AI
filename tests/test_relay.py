"""Frame relay HTTP server."""

from __future__ import annotations

from urllib.request import urlopen

import pytest

from navigator.core.settings import settings
from navigator.meeting.relay import (
    SCREENCAST_EVERY_NTH_FRAME,
    fps_for_meeting_url,
    relay_fps_cap,
    screencast_every_nth,
    screencast_max_size,
    start_relay,
    view_frame_ms,
)


def test_view_and_screencast_tuned_for_smoothness(monkeypatch):
    # Attendee cap lifted so the platform rate is visible. Local is 60fps
    # at 1920×1080. A slower request still polls at that rate.
    monkeypatch.setattr(settings, "screenshare_fps", None)
    monkeypatch.setattr(settings, "attendee_max_fps", 60)
    monkeypatch.setattr(settings, "meeting_url", "")
    assert SCREENCAST_EVERY_NTH_FRAME >= 1
    assert view_frame_ms(20) == 50
    assert view_frame_ms(60) == 17
    assert screencast_every_nth(20) == 3
    assert screencast_every_nth() == 1
    w, h = screencast_max_size()
    assert (w, h) == (1920, 1080)


@pytest.mark.parametrize(
    ("url", "fps"),
    [
        ("https://meet.google.com/abc-defg-hij", 30),
        ("http://meet.google.com/lookup/xyz", 30),
        ("meet.google.com/abc-defg-hij", 30),
        ("HTTPS://MEET.GOOGLE.COM/abc", 30),
        ("https://meet.google.com/abc-defg-hij?authuser=0&hs=1", 30),
        ("https://x.meet.google.com/abc", 30),
        ("https://zoom.us/j/123456789", 60),
        ("https://company.zoom.us/j/1", 60),
        ("https://teams.microsoft.com/l/meetup-join/19%3ameeting", 60),
        ("https://teams.live.com/meet/123", 60),
        ("https://meet.google.com.evil.com/abc", 60),
        ("https://notmeet.google.com/abc", 60),
        ("https://meet.google.com.attacker.com/x?q=1", 60),
        ("", 60),
        (None, 60),
        ("http://127.0.0.1:8000/view", 60),
    ],
)
def test_fps_for_meeting_url(url, fps):
    assert fps_for_meeting_url(url) == fps


def test_relay_cap_follows_the_url_unless_the_env_lowers_it(monkeypatch):
    monkeypatch.setattr(settings, "screenshare_fps", None)
    monkeypatch.setattr(settings, "attendee_max_fps", 60)
    monkeypatch.setattr(settings, "meeting_url", "")
    meet = "https://meet.google.com/abc-defg-hij"
    assert relay_fps_cap(meet) == 30
    assert relay_fps_cap("https://zoom.us/j/1") == 60
    assert relay_fps_cap("https://teams.microsoft.com/l/meetup-join/x") == 60
    assert relay_fps_cap("") == 60
    assert view_frame_ms(60, meet) == 33  # requested 60, Meet cap is 30
    assert screencast_every_nth(meeting_url=meet) == 2
    monkeypatch.setattr(settings, "screenshare_fps", 10)
    assert relay_fps_cap(meet) == 10
    monkeypatch.setattr(settings, "screenshare_fps", 60)
    # 60 does not raise a Meet link above 30.
    assert relay_fps_cap(meet) == 30
    assert relay_fps_cap("") == 60


def test_attendee_max_fps_clamps_encode_rate(monkeypatch):
    monkeypatch.setattr(settings, "screenshare_fps", None)
    monkeypatch.setattr(settings, "attendee_max_fps", 15)
    meet = "https://meet.google.com/abc-defg-hij"
    zoom = "https://zoom.us/j/1"
    assert relay_fps_cap(meet) == 15
    assert relay_fps_cap(zoom) == 15
    assert relay_fps_cap("") == 15
    monkeypatch.setattr(settings, "attendee_max_fps", 30)
    assert relay_fps_cap(meet) == 30
    assert relay_fps_cap(zoom) == 30
    monkeypatch.setattr(settings, "screenshare_fps", 10)
    assert relay_fps_cap(zoom) == 10
    monkeypatch.setattr(settings, "screenshare_fps", 60)
    monkeypatch.setattr(settings, "attendee_max_fps", 15)
    assert relay_fps_cap(zoom) == 15


def test_two_relays_keep_meet_and_zoom_fps(monkeypatch):
    monkeypatch.setattr(settings, "screenshare_fps", None)
    monkeypatch.setattr(settings, "attendee_max_fps", 60)
    monkeypatch.setattr(settings, "meeting_url", "https://meet.google.com/should-not-leak")
    meet = start_relay(meeting_url="https://meet.google.com/abc-defg-hij")
    zoom = start_relay(meeting_url="https://zoom.us/j/123456")
    try:
        assert meet.capture_fps() == 30
        assert zoom.capture_fps() == 60
        with urlopen(meet.view_url, timeout=5) as resp:
            meet_html = resp.read().decode()
        with urlopen(zoom.view_url, timeout=5) as resp:
            zoom_html = resp.read().decode()
        assert "FPS=30" in meet_html
        assert "FPS=60" in zoom_html
        assert meet.capture_fps() == 30
        assert zoom.capture_fps() == 60
    finally:
        meet.stop()
        zoom.stop()


def test_view_returns_html():
    relay = start_relay()
    try:
        with urlopen(relay.view_url, timeout=5) as resp:
            body = resp.read().decode()
            assert resp.status == 200
        assert "getUserMedia" not in body  # screenshare page — no mic requirement
        assert "captureStream(FPS)" in body and "FPS=15" in body
        assert "width=1920" in body and "height=1080" in body
        assert "<video" in body
        assert "/screen.ws" in body
        assert "frame.jpg" not in body
        assert "id=badge" not in body and 'id="badge"' not in body
        assert "tickStatus" not in body
        with urlopen(relay.agent_url, timeout=5) as resp:
            agent = resp.read().decode()
            assert resp.status == 200
        assert "getUserMedia" in agent  # Attendee still needs mic
        assert "frameModel" not in agent
        assert "three.module" not in agent
        assert "avatar.glb" not in agent
        assert "pollState" not in agent
        assert "status-label" not in agent
        assert "avatar-state" not in agent
    finally:
        relay.stop()


def test_status_endpoint_and_set_status():
    import json

    relay = start_relay()
    try:
        relay.set_status("speaking", "Speaking…")
        with urlopen(f"{relay.view_url.rsplit('/', 1)[0]}/status", timeout=5) as resp:
            data = json.loads(resp.read().decode())
            assert resp.status == 200
        assert data["mode"] == "speaking"
        assert "Speaking" in data["label"]
        relay.set_status("listening", "Listening…")
        with urlopen(f"http://{relay.host}:{relay.port}/status", timeout=5) as resp:
            data = json.loads(resp.read().decode())
        assert data["mode"] == "listening"
    finally:
        relay.stop()


def test_stream_serves_latest_frame_only():
    import threading
    import time

    relay = start_relay()
    try:
        relay.set_frame(b"A" * 10)
        relay.set_frame(b"B" * 10)  # A is stale and must never be sent
        resp = urlopen(f"http://127.0.0.1:{relay.port}/stream", timeout=5)
        assert resp.headers["Content-Type"].startswith("multipart/x-mixed-replace")
        parts = []

        def read():
            while len(parts) < 2:
                line = resp.readline()
                if line.startswith(b"Content-Length:"):
                    n = int(line.split(b":")[1]); resp.readline(); parts.append(resp.read(n))

        t = threading.Thread(target=read, daemon=True); t.start()
        time.sleep(0.3)
        relay.set_frame(b"C" * 10)
        t.join(timeout=3)
        assert parts == [b"B" * 10, b"C" * 10]
    finally:
        relay.stop()
