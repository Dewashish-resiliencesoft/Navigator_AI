"""Where the recorder browser runs.

Production default: empty URL → Chromium launches on the API host (today's
behavior). A local Playwright server is opt-in via Platform env only.

Never read a WS URL from a request body, query, or X-Forwarded-For. A Client
JWT must not be able to point Playwright at metadata/internal hosts (SSRF).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

RECORD_WS_PORT = 3333

_BLOCKED_NAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata.google.com",
        "metadata.aws.internal",
    }
)


def safe_record_ws_url(url: str) -> str:
    """Validate a Platform-configured record WS URL. Empty → server launch."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"ws", "wss"}:
        raise ValueError("record ws must be ws:// or wss://")
    if parsed.username or parsed.password:
        raise ValueError("record ws must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("record ws must not include query or fragment")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("record ws missing host")
    if host in _BLOCKED_NAMES:
        raise ValueError("record ws host not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    ):
        raise ValueError("record ws host not allowed")
    if parsed.port is None:
        raise ValueError("record ws must include an explicit port")
    path = parsed.path or ""
    return f"{parsed.scheme}://{host}:{parsed.port}{path}"


def join_ws_path(url: str, path_token: str) -> str:
    """Append path token when the URL has no path yet."""
    base = safe_record_ws_url(url)
    if not base:
        return ""
    token = (path_token or "").strip().lstrip("/")
    if not token:
        return base
    parsed = urlparse(base)
    if (parsed.path or "/").strip("/"):
        return base
    return f"{base.rstrip('/')}/{token}"


def _peer_ws(peer_ip: str, path_token: str) -> str:
    """Lab-only: TCP peer → ws://peer:3333/token. RFC1918 only."""
    try:
        ip = ipaddress.ip_address((peer_ip or "").strip())
    except ValueError:
        return ""
    if not ip.is_private or ip.is_loopback or ip.is_link_local:
        return ""
    token = (path_token or "").strip().lstrip("/")
    path = f"/{token}" if token else ""
    return safe_record_ws_url(f"ws://{ip}:{RECORD_WS_PORT}{path}")


def resolve_record_browser_ws(
    *,
    configured: str,
    path_token: str = "",
    peer_ip: str | None = None,
    record_local: bool = False,
) -> str:
    """Pick recorder browser target. Configured env always wins.

    ``record_local`` uses the TCP peer only (never X-Forwarded-For). Off in
    production so a tenant cannot steer the server at an internal host.
    """
    configured = (configured or "").strip()
    if configured:
        return join_ws_path(configured, path_token)
    if record_local and peer_ip:
        return _peer_ws(peer_ip, path_token)
    return ""
