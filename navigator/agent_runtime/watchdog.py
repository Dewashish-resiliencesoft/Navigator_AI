"""Phase-1: Execution watchdog — action timeout, loop detector, freeze detection.

The orchestrator calls `tick()` after every action starts and `clear()` after
every verified completion. If `tick()` finds an elapsed time exceeding
``action_timeout_ms`` it sets ``timed_out = True`` so the orchestrator can
recover rather than hang silently.

Loop detection fingerprints (url + dom_hash) — same state visited twice in one
flow means we are going in circles.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from navigator.agent_runtime.models import WatchdogSlice, utc_now


_MAX_CONSECUTIVE_FAILURES = 3
_MAX_LOOP_REENTRY = 2


def state_fingerprint(url: str, dom_elements: list[Any]) -> str:
    """Stable fingerprint: url-path + count + sorted element ids."""
    from urllib.parse import urlparse
    path = urlparse(url).path.rstrip("/") or "/"
    ids = sorted(
        str(el.get("testid") or el.get("id") or el.get("text") or "")[:32]
        for el in (dom_elements or [])
    )
    raw = f"{path}|{len(ids)}|{'|'.join(ids[:20])}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]  # noqa: S324


def tick(watchdog: WatchdogSlice, *, started_at: float | None = None) -> WatchdogSlice:
    """Called when an action starts. Returns updated slice."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return watchdog.model_copy(update={
        "last_action_started_at": now,
        "timed_out": False,
    })


def check_timeout(watchdog: WatchdogSlice) -> bool:
    """True if the running action has exceeded action_timeout_ms."""
    if watchdog.last_action_started_at is None:
        return False
    from datetime import datetime, timezone
    elapsed_ms = (datetime.now(timezone.utc) - watchdog.last_action_started_at).total_seconds() * 1000
    return elapsed_ms > watchdog.action_timeout_ms


def record_state(watchdog: WatchdogSlice, fingerprint: str) -> WatchdogSlice:
    """Track visited state. Returns updated slice with loop_detected flag."""
    count = watchdog.visited_states.count(fingerprint)
    loop = count >= _MAX_LOOP_REENTRY
    return watchdog.model_copy(update={
        "visited_states": [*watchdog.visited_states[-100:], fingerprint],
        "loop_detected": loop,
    })


def record_failure(watchdog: WatchdogSlice) -> WatchdogSlice:
    n = watchdog.consecutive_failures + 1
    return watchdog.model_copy(update={"consecutive_failures": n})


def clear_failure(watchdog: WatchdogSlice) -> WatchdogSlice:
    return watchdog.model_copy(update={
        "consecutive_failures": 0,
        "timed_out": False,
        "loop_detected": False,
    })


def is_stuck(watchdog: WatchdogSlice) -> bool:
    return (
        watchdog.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES
        or watchdog.loop_detected
        or watchdog.timed_out
    )
