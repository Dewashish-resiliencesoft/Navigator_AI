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
        assert "nav-cursor" not in body  # relay page, not product
        assert "getUserMedia" in body
        assert "/frame.jpg" in body
    finally:
        relay.stop()
