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
    """For headful Chromium: set DISPLAY when Xvfb/:0 exists, else raise.

    VPS often runs Xvfb on :0 but uvicorn has no DISPLAY → Missing X server.
    """
    current = (os.environ.get("DISPLAY") or "").strip()
    if current:
        return current
    # Common lab Xvfb: /tmp/.X0-lock or /tmp/.X99-lock
    for n in (0, 99, 1):
        if Path(f"/tmp/.X{n}-lock").is_file():
            os.environ["DISPLAY"] = f":{n}"
            print(f"[playwright] DISPLAY unset — using :{n} (Xvfb lock found)", flush=True)
            return f":{n}"
    raise RuntimeError(
        "Headed Chromium needs a display (Missing X server / $DISPLAY). "
        "On a VPS either: (1) run Xvfb and set DISPLAY=:0 for uvicorn, or "
        "(2) record on your laptop — start "
        "`.venv/bin/python scripts/local_record_server.py` and set "
        "NAVIGATOR_RECORD_BROWSER_WS=ws://<laptop-lan-ip>:3333 "
        "(plus NAVIGATOR_RECORD_WS_PATH) on the API host."
    )
