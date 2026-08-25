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


def _zak_origin_reachable_once(base: str) -> bool:
    """Single probe — may flake on fresh trycloudflare DNS."""
    base = base.rstrip("/")
    if "trycloudflare.com" in base:
        from navigator.meeting.tunnel import _probe_via_public_dns

        url = f"{base}/openapi.json"
        try:
            with urlopen(url, timeout=5) as resp:
                return 200 <= getattr(resp, "status", 200) < 500
        except HTTPError as exc:
            return 400 <= int(exc.code) < 500
        except URLError as exc:
            last = str(exc)
            if "Name or service not known" not in last and "nodename" not in last.lower():
                return False
            try:
                code = _probe_via_public_dns(url)
            except Exception:
                return False
            return 200 <= int(code) < 500
        except (TimeoutError, OSError):
            return False

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


def _zak_origin_reachable(base: str, *, attempts: int = 5) -> bool:
    """True when our FastAPI answers through this public origin."""
    for attempt in range(max(1, attempts)):
        if _zak_origin_reachable_once(base):
            return True
        if attempt + 1 < attempts:
            time.sleep(1)
    return False


def ensure_public_base_url(*, local_port: int | None = None) -> str:
    """Return a reachable public origin for ZAK; refresh dead quick-tunnels.

    Stale ``*.trycloudflare.com`` URLs leave Zoom bots stuck joining while
    guests see "waiting for the host". Non-tunnel URLs (prod / tests) are
    trusted as configured.
    """
    global _api_tunnel
    local_port = settings.api_port if local_port is None else local_port
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
            live = _api_tunnel.public_url
            if _zak_origin_reachable(live):
                settings.public_base_url = live
                return live
            print("[zoom] stale tunnel process — restarting cloudflared", flush=True)
            _api_tunnel.stop()
            _api_tunnel = None

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
        # Quick tunnels need a moment before the edge routes; dig can flake too.
        for _ in range(30):
            if _zak_origin_reachable(settings.public_base_url, attempts=2):
                break
            time.sleep(1)
        else:
            dead = settings.public_base_url
            _api_tunnel.stop()
            _api_tunnel = None
            settings.public_base_url = ""
            raise RuntimeError(
                f"Zoom host join needs Attendee to reach {dead}/v1/zoom/zak, "
                "but that origin does not answer. Set "
                "NAVIGATOR_PUBLIC_BASE_URL to a stable public origin, or "
                "check that cloudflared is running."
            )
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
