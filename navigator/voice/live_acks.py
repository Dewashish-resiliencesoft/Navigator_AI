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
    "Yeah…",
    "One sec…",
    "Checking that…",
    "Hmm…",
    "Alright…",
)
_HI = (
    "Haan…",
    "Ek second…",
    "Dekh rahi hoon…",
    "Hmm…",
    "Theek hai…",
)

_NUDGE_GAP_S = 2.0
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
    global _last_nudge_at
    now = time.monotonic()
    with _lock:
        if now - _last_nudge_at < _NUDGE_GAP_S:
            return False
        _last_nudge_at = now
        text = next(_hi_cycle) if language == "hi" else next(_en_cycle)
    live.nudge(text)  # type: ignore[union-attr]
    return True
