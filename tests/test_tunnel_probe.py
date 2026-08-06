"""Tunnel probe helpers."""

from navigator.meeting.tunnel import (
    _extract_quick_tunnel_url,
    _probe_reachable_status,
    is_quick_tunnel_url,
)


def test_probe_reachable_accepts_403():
    assert _probe_reachable_status(403) is True


def test_probe_reachable_accepts_200():
    assert _probe_reachable_status(200) is True


def test_probe_reachable_rejects_502():
    assert _probe_reachable_status(502) is False


def test_quick_tunnel_url_rejects_api_host():
    assert is_quick_tunnel_url("https://api.trycloudflare.com") is False
    assert is_quick_tunnel_url("https://foo-bar.trycloudflare.com") is True


def test_extract_quick_tunnel_url_skips_api_banner_line():
    line = "Visit https://api.trycloudflare.com for docs or https://relay-zone-mineral-mae.trycloudflare.com now"
    assert _extract_quick_tunnel_url(line) == "https://relay-zone-mineral-mae.trycloudflare.com"
