"""One screenshot, only when the demo needs eyes.

The screenshare is already a live video. This module does not poll frames.
It grabs a single viewport PNG when the visitor asks where we are, an
element cannot be found, or a step is stuck, and asks gemini-3.6-flash
what is on screen.
"""

from __future__ import annotations

import inspect
import re
import threading
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
#: A vision call that never returns must not hold the demo or another session.
VISION_TIMEOUT_S = 8.0

_guard = threading.Lock()
_sessions: dict[str, "_Look"] = {}


class _Look:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last_at = 0.0
        self.last_text = ""
        self.last_until = 0.0


def _bucket(session_id: str) -> _Look:
    key = session_id or ""
    with _guard:
        bucket = _sessions.get(key)
        if bucket is None:
            bucket = _Look()
            _sessions[key] = bucket
        return bucket


def asks_where(utterance: str) -> bool:
    """True when the visitor is asking what the screen is showing."""
    return bool(_WHERE.search(utterance or ""))


def last_look(session_id: str = "") -> str:
    """The latest vision note for this session, while it is still fresh."""
    bucket = _bucket(session_id)
    if time.monotonic() > bucket.last_until:
        return ""
    return bucket.last_text


def reset_look(session_id: str | None = None) -> None:
    """Test hook. Clears one session, or every session when id is omitted."""
    with _guard:
        if session_id is None:
            _sessions.clear()
            return
        _sessions.pop(session_id or "", None)


def look_once(page, *, reason: str, complete=None, session_id: str = "") -> str:
    """Capture one screenshot for ``reason`` and describe it.

    Rate limiting is per session. A hung model call is abandoned after
    ``VISION_TIMEOUT_S`` and does not spend another session's budget.
    """
    print(f"[vision] trigger={reason} session={session_id or '-'}", flush=True)
    bucket = _bucket(session_id)
    now = time.monotonic()
    with bucket.lock:
        if bucket.last_at and now - bucket.last_at < _MIN_GAP_S:
            print(f"[vision] skipped: rate limit reason={reason}", flush=True)
            return ""
        # Reserve only this session. The model call stays outside the lock.
        bucket.last_at = now
    if page is None:
        print(f"[vision] skipped: no page reason={reason}", flush=True)
        return ""
    try:
        png = _screenshot(page, VISION_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        print(f"[vision] screenshot failed ({reason}): {exc}", flush=True)
        return ""
    png = _downscale(png)
    if complete is None:
        from navigator.agent.providers import get_provider

        complete = get_provider().complete_with_image
    system = (
        "You describe one product screenshot for a live demo. "
        "Two short sentences: what is visible, and where to go next. "
        "No markdown."
    )
    user = f"Reason for looking: {reason}."
    try:
        text = (_invoke(complete, system, user, png, VISION_TIMEOUT_S) or "").strip()
    except TimeoutError:
        print(f"[vision] timed out ({reason})", flush=True)
        return ""
    except Exception as exc:  # noqa: BLE001
        print(f"[vision] model failed ({reason}): {exc}", flush=True)
        return ""
    with bucket.lock:
        bucket.last_text = text
        bucket.last_until = now + 30.0
    print(f"[vision] {reason}: {text[:160]}", flush=True)
    return text


def _screenshot(page, timeout_s: float) -> bytes:
    fn = page.screenshot
    if inspect.iscoroutinefunction(fn):
        from navigator.automation.browser.bounded import run_limited

        return run_limited(fn(type="png", full_page=False), timeout_s)
    timeout_ms = max(1, int(timeout_s * 1000))
    try:
        return fn(type="png", full_page=False, timeout=timeout_ms)
    except TypeError:
        return fn(type="png", full_page=False)


def _invoke(complete, system: str, user: str, png: bytes, timeout_s: float) -> str:
    if inspect.iscoroutinefunction(complete):
        from navigator.automation.browser.bounded import run_limited

        return run_limited(complete(system, user, png), timeout_s)
    box: dict = {}

    def _run() -> None:
        try:
            box["value"] = complete(system, user, png)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise TimeoutError("vision timed out")
    if "error" in box:
        raise box["error"]
    return box.get("value") or ""


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
