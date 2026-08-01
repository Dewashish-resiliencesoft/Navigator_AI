"""Host environment + redacted meeting labels for demo_runs."""

from __future__ import annotations

import platform
import re
import socket
from urllib.parse import urlparse


def capture_host_meta() -> dict[str, str]:
    return {
        "host_os": platform.system() or "",
        "host_release": platform.release() or "",
        "host_machine": platform.machine() or "",
        "host_name": socket.gethostname().split(".")[0][:64],
        "browser": "",
    }


def meeting_label(url: str | None, platform_name: str | None = None) -> str:
    if not url:
        return (platform_name or "")[:32]
    parsed = urlparse(url.strip().split("#", 1)[0].split("?", 1)[0])
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").strip("/")
    if "meet.google" in host:
        code = path.split("/")[-1] if path else ""
        code = re.sub(r"[^a-z0-9-]", "", code.lower())[:32]
        return f"meet:{code}" if code else "meet"
    if "zoom" in host:
        m = re.search(r"(\d{9,})", path)
        return f"zoom:{m.group(1)}" if m else "zoom"
    return (platform_name or host or "meeting")[:64]
