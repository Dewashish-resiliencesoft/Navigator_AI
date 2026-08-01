"""Zoom host (ZAK) helpers — kept free of live_demo/graph imports."""

from __future__ import annotations

from urllib.parse import urlencode

from navigator.core.settings import settings


def is_zoom_meeting(meeting_url: str) -> bool:
    u = (meeting_url or "").lower()
    if "zoom.us/" in u or "zoom.com/" in u:
        return True
    if "meet.google.com" in u:
        return False
    return settings.meeting_platform == "zoom"


def zoom_zak_callback_url() -> str:
    """Public URL Attendee POSTs to mint a ZAK (host start)."""
    base = (settings.public_base_url or "").rstrip("/")
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
