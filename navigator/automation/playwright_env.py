"""Fix Playwright browser path when Cursor sandbox cache is empty/stale."""

from __future__ import annotations

import os
from pathlib import Path


def _chromium_present(root: Path) -> bool:
    if not root.is_dir():
        return False
    for pattern in (
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
        "chromium-*/chrome-linux64/chrome",
        "chromium-*/chrome-linux/chrome",
    ):
        if any(root.glob(pattern)):
            return True
    return False


def ensure_playwright_browsers() -> str | None:
    """Point PLAYWRIGHT_BROWSERS_PATH at a cache that actually has Chromium.

    Cursor often injects ``PLAYWRIGHT_BROWSERS_PATH=/tmp/cursor-sandbox-cache/...``
    which can be empty after a sandbox recycle. Prefer that path when populated;
    otherwise fall back to ``~/.cache/ms-playwright`` (or unset for Playwright default).

    Returns the active browsers path (or None if left to Playwright default).
    """
    configured = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    home_cache = Path.home() / ".cache" / "ms-playwright"

    if configured and _chromium_present(Path(configured)):
        return configured

    if _chromium_present(home_cache):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(home_cache)
        if configured and configured != str(home_cache):
            print(
                f"[playwright] {configured!r} missing Chromium — "
                f"using {home_cache}",
                flush=True,
            )
        return str(home_cache)

    # Broken sandbox path with no home cache: unset so install error is clearer.
    if configured:
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        print(
            f"[playwright] {configured!r} missing Chromium and "
            f"{home_cache} empty — unset PLAYWRIGHT_BROWSERS_PATH. "
            "Run: .venv/bin/python -m playwright install chromium",
            flush=True,
        )
    return None


def ensure_headed_display() -> str | None:
    """For headful Chromium: set DISPLAY when a usable X server exists.

    Prefer an already-working ``DISPLAY``. If unset or unauthorized (common on
    VPS when uvicorn has ``DISPLAY=:0`` but LightDM xauth blocks the app user),
    fall back to Xvfb locks ``:99``, ``:0``, ``:1``.
    """
    candidates: list[str] = []
    current = (os.environ.get("DISPLAY") or "").strip()
    if current:
        candidates.append(current)
    for n in (99, 0, 1):
        disp = f":{n}"
        if disp not in candidates and Path(f"/tmp/.X{n}-lock").is_file():
            candidates.append(disp)
        # Also accept /tmp/.X11-unix/Xn sockets (Xvfb -displayfd may omit lock).
        sock = Path(f"/tmp/.X11-unix/X{n}")
        if disp not in candidates and sock.exists():
            candidates.append(disp)

    for disp in candidates:
        if _display_usable(disp):
            if os.environ.get("DISPLAY") != disp:
                os.environ["DISPLAY"] = disp
                print(f"[playwright] using DISPLAY={disp}", flush=True)
            return disp

    raise RuntimeError(
        "Headed Chromium needs a display (Missing X server / $DISPLAY). "
        "On a VPS either: (1) run `Xvfb :99 -screen 0 1920x1080x24 -ac` and "
        "start uvicorn with DISPLAY=:99, or (2) record on your laptop — start "
        "`.venv/bin/python scripts/local_record_server.py` and set "
        "NAVIGATOR_RECORD_BROWSER_WS=ws://<laptop-lan-ip>:3333 "
        "(plus NAVIGATOR_RECORD_WS_PATH) on the API host."
    )


def _display_usable(display: str) -> bool:
    """True if Chromium could open this DISPLAY (xdpyinfo or socket+no auth fail)."""
    import subprocess

    try:
        r = subprocess.run(
            ["xdpyinfo"],
            env={**os.environ, "DISPLAY": display},
            capture_output=True,
            timeout=3,
            check=False,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # No xdpyinfo — accept only if socket exists (best-effort).
        n = display.lstrip(":").split(".")[0]
        return Path(f"/tmp/.X11-unix/X{n}").exists()
