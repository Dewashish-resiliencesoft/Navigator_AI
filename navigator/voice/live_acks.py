"""Short spoken acks while the director runs browser work.

Fire-and-forget lines for Gemini Live so the meeting never goes silent during a
click/fill/navigate. No tenant names — plain human filler only.
"""

from __future__ import annotations

import itertools
import threading
import time
from typing import Literal

SpokenLanguage = Literal["en", "hi"]

_EN = (
    "One sec…",
    "Checking that…",
    "Alright…",
)
_HI = (
    "Ek second…",
    "Dekh rahi hoon…",
    "Theek hai…",
)

_NUDGE_GAP_S = 8.0
_lock = threading.Lock()
_last_nudge_at = 0.0
_en_cycle = itertools.cycle(_EN)
_hi_cycle = itertools.cycle(_HI)


def reset_nudge_throttle_for_tests() -> None:
    """Test helper: allow the next nudge immediately."""
    global _last_nudge_at
    with _lock:
        _last_nudge_at = 0.0


def next_working_ack(language: SpokenLanguage = "en") -> str:
    """Rotate a short ack for the current spoken language."""
    with _lock:
        if language == "hi":
            return next(_hi_cycle)
        return next(_en_cycle)


def maybe_nudge_live(live: object | None, *, language: SpokenLanguage = "en") -> bool:
    """Speak a working ack if Live is present and the throttle allows it.

    Returns True when a nudge was queued.
    """
    if live is None or not hasattr(live, "nudge"):
        return False
    # Don't talk over a line already in flight — "Yeah…" on top of narration
    # is the agent answering itself.
    if getattr(live, "speaking", False):
        return False
    can_start = getattr(live, "can_start_utterance", None)
    if callable(can_start) and not can_start():
        return False
    global _last_nudge_at
    now = time.monotonic()
    with _lock:
        if now - _last_nudge_at < _NUDGE_GAP_S:
            return False
        _last_nudge_at = now
        text = next(_hi_cycle) if language == "hi" else next(_en_cycle)
    live.nudge(text)  # type: ignore[union-attr]
    return True
