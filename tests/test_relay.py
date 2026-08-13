"""Frame relay HTTP server."""

from __future__ import annotations

from urllib.request import urlopen

from navigator.meeting.relay import (
    SCREENCAST_EVERY_NTH_FRAME,
    VIEW_FRAME_MS,
    resolve_avatar_glb,
    start_relay,
)


def test_view_and_screencast_tuned_for_smoothness():
    # 30fps view swap + every-frame screencast: smooth once the CPU hog
    # (Attendee's ffmpeg recording) is disabled.
    assert VIEW_FRAME_MS == 33
    assert SCREENCAST_EVERY_NTH_FRAME == 1


def test_view_returns_html():
    relay = start_relay()
    try:
        with urlopen(relay.view_url, timeout=5) as resp:
            body = resp.read().decode()
            assert resp.status == 200
        assert "getUserMedia" not in body  # screenshare page — no mic requirement
        assert "/frame.jpg" in body
        assert "setTimeout(tickFrame, 33)" in body
        assert 'id=badge' in body or 'id="badge"' in body
        assert "/status" in body
        with urlopen(relay.agent_url, timeout=5) as resp:
            agent = resp.read().decode()
            assert resp.status == 200
        assert "getUserMedia" in agent
        assert "frameModel" in agent  # auto-fit camera for dropped GLBs
    finally:
        relay.stop()


def test_resolve_avatar_glb_finds_female():
    path = resolve_avatar_glb()
    assert path is not None
    assert path.name == "female_avatar.glb"
    assert path.stat().st_size > 1000


def test_avatar_glb_served():
    relay = start_relay()
    try:
        with urlopen(f"http://{relay.host}:{relay.port}/avatar.glb", timeout=5) as resp:
            data = resp.read()
            assert resp.status == 200
        assert data[:4] == b"glTF"
        assert len(data) > 1000
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
