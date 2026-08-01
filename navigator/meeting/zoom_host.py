"""Zoom host (ZAK) helpers — kept free of live_demo/graph imports."""

from __future__ import annotations

import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from navigator.core.settings import settings

_api_tunnel = None
_api_tunnel_lock = threading.Lock()


def is_zoom_meeting(meeting_url: str) -> bool:
    u = (meeting_url or "").lower()
    if "zoom.us/" in u or "zoom.com/" in u:
        return True
    if "meet.google.com" in u:
        return False
    return settings.meeting_platform == "zoom"


def _zak_origin_reachable(base: str) -> bool:
    """True when our FastAPI answers through this public origin.

    Local stub DNS often cannot resolve fresh ``*.trycloudflare.com`` names, so
    those are probed via dig@1.1.1.1 (same trick as screenshare tunnels).
    """
    base = base.rstrip("/")
    if "trycloudflare.com" in base:
        from navigator.meeting.tunnel import _probe_via_public_dns

        try:
            code = _probe_via_public_dns(f"{base}/openapi.json")
        except Exception:
            return False
        return 200 <= int(code) < 500

    url = f"{base}/v1/zoom/zak"
    try:
        req = Request(
            url,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=5) as resp:
            resp.read(64)
        return True
    except HTTPError as exc:
        return 400 <= int(exc.code) < 500
    except (URLError, TimeoutError, OSError):
        return False


def ensure_public_base_url(*, local_port: int = 8000) -> str:
    """Return a reachable public origin for ZAK; refresh dead quick-tunnels.

    Stale ``*.trycloudflare.com`` URLs leave Zoom bots stuck joining while
    guests see "waiting for the host". Non-tunnel URLs (prod / tests) are
    trusted as configured.
    """
    global _api_tunnel
    base = (settings.public_base_url or "").rstrip("/")
    if base and "trycloudflare.com" not in base:
        return base
    if base and _zak_origin_reachable(base):
        return base

    with _api_tunnel_lock:
        base = (settings.public_base_url or "").rstrip("/")
        if base and "trycloudflare.com" not in base:
            return base
        if base and _zak_origin_reachable(base):
            return base
        if _api_tunnel is not None and _api_tunnel._proc.poll() is None:
            settings.public_base_url = _api_tunnel.public_url
            return _api_tunnel.public_url

        from navigator.meeting.tunnel import start_tunnel

        print(
            f"[zoom] public base missing/dead — tunneling :{local_port}",
            flush=True,
        )
        # Skip /view probe (screenshare-only). Verify with ZAK route below.
        _api_tunnel = start_tunnel(
            local_port, binary=settings.tunnel_bin, ready_path=None
        )
        settings.public_base_url = _api_tunnel.public_url
        # Quick tunnels need a moment before the edge routes.
        for _ in range(15):
            if _zak_origin_reachable(settings.public_base_url):
                break
            time.sleep(1)
        print(f"[zoom] public base: {settings.public_base_url}", flush=True)
        return settings.public_base_url


def zoom_zak_callback_url() -> str:
    """Public URL Attendee POSTs to mint a ZAK (host start)."""
    base = ensure_public_base_url().rstrip("/")
    if not base:
        raise RuntimeError(
            "NAVIGATOR_PUBLIC_BASE_URL is required for Zoom host join "
            "(Attendee must reach POST /v1/zoom/zak)"
        )
    url = f"{base}/v1/zoom/zak"
    secret = (settings.zoom_zak_callback_secret or "").strip()
    if secret:
        url = f"{url}?{urlencode({'secret': secret})}"
    return url
