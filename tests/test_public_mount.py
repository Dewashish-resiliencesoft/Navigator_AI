"""Public mount helpers — one stable origin instead of N quick tunnels."""

from __future__ import annotations

from navigator.meeting import public_mount as pm


def test_publish_http_registers_and_looks_up(monkeypatch):
    monkeypatch.setattr(
        "navigator.meeting.public_mount.ensure_public_base_url"
        if False
        else "navigator.meeting.zoom_host.ensure_public_base_url",
        lambda local_port=None: "https://example.trycloudflare.com",
    )
    # ensure is imported inside _require_public_base from zoom_host
    monkeypatch.setattr(
        "navigator.meeting.zoom_host.ensure_public_base_url",
        lambda local_port=None: "https://example.trycloudflare.com",
    )
    handle = pm.publish_http("127.0.0.1", 9999)
    assert handle.public_url.startswith("https://example.trycloudflare.com/v1/live-http/")
    token = handle._token
    assert pm.lookup_http(token) == ("127.0.0.1", 9999)
    handle.stop()
    assert pm.lookup_http(token) is None


def test_publish_ws_registers(monkeypatch):
    monkeypatch.setattr(
        "navigator.meeting.zoom_host.ensure_public_base_url",
        lambda local_port=None: "https://example.trycloudflare.com",
    )
    handle = pm.publish_ws("127.0.0.1", 8888)
    assert "/v1/live-ws/" in handle.public_url
    assert pm.lookup_ws(handle._token) == ("127.0.0.1", 8888)
    handle.stop()
