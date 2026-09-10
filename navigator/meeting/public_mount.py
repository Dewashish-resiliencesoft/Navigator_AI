"""Publish local relay/audio ports via NAVIGATOR_PUBLIC_BASE_URL (one tunnel).

Docker quick-tunnels get Cloudflare 429 when every demo opens a fresh
cloudflared. When a stable public origin already fronts uvicorn (:8080/:8000),
mount local HTTP/WS targets under ``/v1/live-http|live-ws/{token}`` instead.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from typing import Any

from navigator.core.settings import settings

_lock = threading.Lock()
_http: dict[str, tuple[str, int]] = {}
_ws: dict[str, tuple[str, int]] = {}


def lookup_http(token: str) -> tuple[str, int] | None:
    with _lock:
        return _http.get(token)


def lookup_ws(token: str) -> tuple[str, int] | None:
    with _lock:
        return _ws.get(token)


def _register(store: dict[str, tuple[str, int]], host: str, port: int) -> str:
    token = secrets.token_urlsafe(12)
    with _lock:
        store[token] = (host or "127.0.0.1", int(port))
    return token


def unregister(token: str) -> None:
    with _lock:
        _http.pop(token, None)
        _ws.pop(token, None)


@dataclass
class MountedPublish:
    """Drop-in for ``TunnelHandle`` when using the API public origin."""

    public_url: str
    _token: str
    _proc: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._proc is None:
            self._proc = _AliveProc()

    def stop(self) -> None:
        unregister(self._token)


class _AliveProc:
    def poll(self) -> None:
        return None


def _require_public_base() -> str:
    from navigator.meeting.zoom_host import ensure_public_base_url

    base = (ensure_public_base_url() or "").rstrip("/")
    if not base:
        raise RuntimeError(
            "NAVIGATOR_PUBLIC_BASE_URL required to publish live relay without "
            "a fresh cloudflared quick tunnel"
        )
    return base


def publish_http(host: str, port: int) -> MountedPublish:
    """HTTPS origin that reverse-proxies to local HTTP (screenshare relay)."""
    base = _require_public_base()
    token = _register(_http, host, port)
    return MountedPublish(
        public_url=f"{base}/v1/live-http/{token}",
        _token=token,
    )


def publish_ws(host: str, port: int) -> MountedPublish:
    """HTTPS origin; callers swap https→wss for Attendee audio."""
    base = _require_public_base()
    token = _register(_ws, host, port)
    return MountedPublish(
        public_url=f"{base}/v1/live-ws/{token}",
        _token=token,
    )


def publish_http_or_tunnel(
    host: str,
    port: int,
    *,
    binary: str = "cloudflared",
    ready_path: str | None = "/view",
) -> Any:
    """Prefer public-base mount; fall back to a quick tunnel."""
    if (settings.public_base_url or "").strip():
        try:
            return publish_http(host, port)
        except Exception as exc:  # noqa: BLE001
            print(f"[tunnel] public mount failed ({exc}); trying cloudflared", flush=True)
    from navigator.meeting.tunnel import start_tunnel

    return start_tunnel(port, binary=binary, ready_path=ready_path)


def publish_ws_or_tunnel(
    host: str,
    port: int,
    *,
    binary: str = "cloudflared",
) -> Any:
    if (settings.public_base_url or "").strip():
        try:
            return publish_ws(host, port)
        except Exception as exc:  # noqa: BLE001
            print(f"[tunnel] public WS mount failed ({exc}); trying cloudflared", flush=True)
    from navigator.meeting.tunnel import start_tunnel

    return start_tunnel(port, binary=binary, ready_path=None)
