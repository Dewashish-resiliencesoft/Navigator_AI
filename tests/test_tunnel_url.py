"""cloudflared quick-tunnel URL parsing + registration wait helpers."""

from __future__ import annotations

from navigator.meeting.tunnel import _URL_RE


def test_url_re_matches_quick_tunnel_hostname():
    line = (
        "INF |  https://local-with-differ-missing.trycloudflare.com                                       |"
    )
    m = _URL_RE.search(line)
    assert m is not None
    assert m.group(0) == "https://local-with-differ-missing.trycloudflare.com"


def test_url_re_rejects_api_trycloudflare():
    assert _URL_RE.search("https://api.trycloudflare.com") is None
    assert _URL_RE.search("wss://api.trycloudflare.com/anything") is None


def test_url_re_rejects_bare_label_without_hyphen():
    # Real quick tunnels always use hyphenated labels.
    assert _URL_RE.search("https://onlyone.trycloudflare.com") is None
