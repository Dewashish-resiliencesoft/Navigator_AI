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
    # Smooth: every repaint (nth=1 setting), 720p frames, MJPEG push.
    assert 42 <= VIEW_FRAME_MS <= 125
    assert SCREENCAST_EVERY_NTH_FRAME >= 1
    assert view_frame_ms(20) == 50
    assert view_frame_ms(60) == 42  # capped to 24fps
    assert screencast_every_nth(20) == 3
    w, h = screencast_max_size()
    assert (w, h) == (1280, 720)


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
