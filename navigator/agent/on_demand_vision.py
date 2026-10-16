"""One screenshot, only when the demo needs eyes.

The screenshare is already a live video. This module does not poll frames.
It grabs a single viewport PNG when the visitor asks where we are, an
element cannot be found, or a step is stuck, and asks gemini-3.6-flash
what is on screen.
"""

from __future__ import annotations

import re
import time
from io import BytesIO

_WHERE = re.compile(
    r"("
    r"where (are|am) (we|i)|"
    r"what(?:'s| is) on (?:the )?screen|"
    r"what am i looking at|"
    r"where (?:do|should) we go|"
    r"what page is this|"
    r"where is this"
    r")",
    re.I,
)

_MIN_GAP_S = 8.0
_last_at = 0.0
_last_text = ""
_last_until = 0.0


def asks_where(utterance: str) -> bool:
    """True when the visitor is asking what the screen is showing."""
    return bool(_WHERE.search(utterance or ""))


def last_look() -> str:
    """The latest vision note, while it is still fresh for the planner."""
    if time.monotonic() > _last_until:
        return ""
    return _last_text


def reset_look() -> None:
    """Test hook."""
    global _last_at, _last_text, _last_until
    _last_at = 0.0
    _last_text = ""
    _last_until = 0.0


def look_once(page, *, reason: str, complete=None) -> str:
    """Capture one screenshot for ``reason`` and describe it.

    A second call inside ``_MIN_GAP_S`` logs the reason and does not
    capture again. Returns "" when there is nothing new to add.
    """
    global _last_at, _last_text, _last_until
    print(f"[vision] trigger={reason}", flush=True)
    now = time.monotonic()
    if _last_at and now - _last_at < _MIN_GAP_S:
        print(f"[vision] skipped: rate limit reason={reason}", flush=True)
        return ""
    if page is None:
        print(f"[vision] skipped: no page reason={reason}", flush=True)
        return ""
    try:
        png = page.screenshot(type="png", full_page=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[vision] screenshot failed ({reason}): {exc}", flush=True)
        return ""
    png = _downscale(png)
    if complete is None:
        from navigator.agent.providers import get_provider

        complete = get_provider().complete_with_image
    _last_at = now
    try:
        text = (
            complete(
                "You describe one product screenshot for a live demo. "
                "Two short sentences: what is visible, and where to go next. "
                "No markdown.",
                f"Reason for looking: {reason}.",
                png,
            )
            or ""
        ).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[vision] model failed ({reason}): {exc}", flush=True)
        return ""
    _last_text = text
    _last_until = now + 30.0
    print(f"[vision] {reason}: {text[:160]}", flush=True)
    return text


def _downscale(png: bytes, max_w: int = 768) -> bytes:
    if not png:
        return png
    try:
        from PIL import Image

        image = Image.open(BytesIO(png))
        if image.width <= max_w:
            return png
        height = max(1, int(image.height * max_w / image.width))
        image = image.resize((max_w, height))
        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return png
