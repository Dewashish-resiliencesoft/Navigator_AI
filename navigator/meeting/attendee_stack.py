"""Ensure self-hosted Attendee is running before live demos.

When ``NAVIGATOR_ATTENDEE_BASE_URL`` points at localhost, Navigator can
``docker compose up -d`` the Attendee stack on startup so dev and VPS behave
the same: meeting bot always reachable, no manual second terminal.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from navigator.core.settings import settings

_COMPOSE_FILES = ("dev.docker-compose.yaml", "local.docker-compose.yaml")
_COMPOSE_PROFILE = "webpage-streamer"


def is_local_attendee_url(base_url: str) -> bool:
    return any(
        h in base_url
        for h in ("localhost", "127.0.0.1", "host.docker.internal")
    )


def attendee_reachable(base_url: str, *, timeout_s: float = 3.0) -> bool:
    """True when Attendee answers at ``base_url`` (401/404 still count as up)."""
    try:
        urlopen(base_url, timeout=timeout_s)
    except HTTPError:
        return True
    except (URLError, OSError):
        return False
    return True


def _in_pytest() -> bool:
    return "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST") is not None


def _compose_dir() -> Path:
    raw = os.environ.get("NAVIGATOR_ATTENDEE_COMPOSE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return settings.attendee_compose_dir.expanduser()


def _docker_compose_up(compose_dir: Path) -> subprocess.CompletedProcess[str]:
    missing = [f for f in _COMPOSE_FILES if not (compose_dir / f).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Attendee compose files missing in {compose_dir}: {', '.join(missing)}"
        )

    cmd = [
        "docker",
        "compose",
        "-f",
        _COMPOSE_FILES[0],
        "-f",
        _COMPOSE_FILES[1],
        "--profile",
        _COMPOSE_PROFILE,
        "up",
        "-d",
    ]
    return subprocess.run(
        cmd,
        cwd=compose_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def ensure_attendee_stack(
    *,
    base_url: str | None = None,
    autostart: bool | None = None,
    compose_dir: Path | None = None,
    wait_timeout_s: float = 180.0,
) -> bool:
    """Start local Attendee via docker compose when needed. Returns True if reachable."""
    base_url = base_url or settings.attendee_base_url
    autostart = settings.attendee_autostart if autostart is None else autostart
    compose_dir = compose_dir or _compose_dir()

    if not autostart or _in_pytest() or not is_local_attendee_url(base_url):
        return attendee_reachable(base_url)

    if attendee_reachable(base_url):
        print(f"[attendee] already up at {base_url}", flush=True)
        return True

    if not compose_dir.is_dir():
        print(
            f"[attendee] WARN: {compose_dir} missing — clone attendee-labs/attendee "
            "or set NAVIGATOR_ATTENDEE_COMPOSE_DIR",
            flush=True,
        )
        return False

    print(f"[attendee] starting docker stack in {compose_dir}…", flush=True)
    try:
        proc = _docker_compose_up(compose_dir)
    except FileNotFoundError as exc:
        print(f"[attendee] WARN: {exc}", flush=True)
        return False

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        print(
            f"[attendee] WARN: docker compose failed ({proc.returncode})"
            + (f": {detail}" if detail else ""),
            flush=True,
        )
        if "permission denied" in detail.lower() or "connect: permission denied" in detail.lower():
            print(
                "[attendee] docker permission denied — run `newgrp docker` or "
                "`sg docker -c 'docker compose … up -d'` from the Attendee clone",
                flush=True,
            )
        return False

    deadline = time.time() + wait_timeout_s
    while time.time() < deadline:
        if attendee_reachable(base_url):
            print(f"[attendee] ready at {base_url}", flush=True)
            return True
        time.sleep(2)

    print(
        f"[attendee] WARN: still unreachable at {base_url} after {wait_timeout_s:.0f}s — "
        "check `docker compose ps` in the Attendee clone",
        flush=True,
    )
    return False
