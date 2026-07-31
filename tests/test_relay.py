"""Frame relay HTTP server."""

from __future__ import annotations

from urllib.request import urlopen

from navigator.meeting.relay import start_relay


def test_view_returns_html():
    relay = start_relay()
    try:
        with urlopen(relay.view_url, timeout=5) as resp:
            body = resp.read().decode()
            assert resp.status == 200
        assert "getUserMedia" not in body  # screenshare page — no mic requirement
        assert "/frame.jpg" in body
        with urlopen(relay.agent_url, timeout=5) as resp:
            agent = resp.read().decode()
            assert resp.status == 200
        assert "getUserMedia" in agent
    finally:
        relay.stop()
