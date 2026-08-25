"""Public-origin probe for Zoom ZAK callback."""

from __future__ import annotations

from unittest.mock import patch

from navigator.meeting import zoom_host


def test_zak_origin_reachable_retries_flaky_probe(monkeypatch):
    calls = {"n": 0}

    def flaky(_base: str) -> bool:
        calls["n"] += 1
        return calls["n"] >= 2

    monkeypatch.setattr(zoom_host, "_zak_origin_reachable_once", flaky)
    monkeypatch.setattr(zoom_host.time, "sleep", lambda _s: None)
    assert zoom_host._zak_origin_reachable("https://x.trycloudflare.com", attempts=3)
    assert calls["n"] == 2


def test_dig_ips_falls_back_to_socket_when_dig_empty(monkeypatch):
    from navigator.meeting import tunnel

    monkeypatch.setattr(
        tunnel.subprocess,
        "check_output",
        lambda *a, **k: "",
    )
    monkeypatch.setattr(tunnel, "_socket_resolve", lambda _h: ["203.0.113.1"])
    assert tunnel._dig_ips("fresh.trycloudflare.com") == ["203.0.113.1"]
