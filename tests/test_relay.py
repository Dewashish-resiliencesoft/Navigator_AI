"""Frame relay HTTP server."""

from __future__ import annotations

from urllib.request import urlopen

from navigator.meeting.relay import (
    SCREENCAST_EVERY_NTH_FRAME,
    VIEW_FRAME_MS,
    screencast_every_nth,
    screencast_max_size,
    start_relay,
    view_frame_ms,
)


def test_view_and_screencast_tuned_for_smoothness():
    # Tunnel-friendly: ~20fps poll, CDP subsampled, 960-wide frames.
    assert 42 <= VIEW_FRAME_MS <= 125
    assert SCREENCAST_EVERY_NTH_FRAME >= 1
    assert view_frame_ms(20) == 50
    assert view_frame_ms(60) == 42  # capped to 24fps
    assert screencast_every_nth(20) == 3
    w, h = screencast_max_size()
    assert w == 960
    assert h == 540


def test_view_returns_html():
    relay = start_relay()
    try:
        with urlopen(relay.view_url, timeout=5) as resp:
            body = resp.read().decode()
            assert resp.status == 200
        assert "getUserMedia" not in body  # screenshare page — no mic requirement
        assert "frame.jpg" in body
        assert f"setInterval(pullFrame, {VIEW_FRAME_MS})" in body
        assert "inFlight" in body
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
