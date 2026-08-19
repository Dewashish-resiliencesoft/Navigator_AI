"""Backchannel rate limiter for Gemini Live.

Controls WHEN the voice agent may produce a short contextual response.
Gemini Live controls WHAT to say — never a static list.

The controller gates the nudge call; the model generates the wording
from the current conversation context.
"""

from __future__ import annotations

import threading
import time


class BackchannelController:
    """Rate-limit short acknowledgements so Live doesn't over-respond.

    Args:
        min_interval_s: Minimum seconds between any two backchannels.
        max_per_turn: Maximum backchannels emitted while the user holds the floor.
        nudge_fn: Callable that sends a nudge instruction to Gemini Live.
                  Receives the contextual hint string; Live generates the words.
    """

    def __init__(
        self,
        *,
        min_interval_s: float = 3.0,
        max_per_turn: int = 1,
        nudge_fn=None,
    ) -> None:
        self._min_interval_s = min_interval_s
        self._max_per_turn = max_per_turn
        self._nudge_fn = nudge_fn
        self._lock = threading.Lock()
        self._last_backchannel_at: float = 0.0
        self._this_turn_count: int = 0

    def reset_turn(self) -> None:
        """Call when a new user turn starts."""
        with self._lock:
            self._this_turn_count = 0

    def maybe_backchannel(self, *, context_hint: str = "") -> bool:
        """Attempt to emit a contextual backchannel.

        Returns True if a nudge was sent; False if rate-limited or no nudge_fn.
        The nudge hint is a conversational cue — Gemini Live decides the words.
        """
        if self._nudge_fn is None:
            return False

        now = time.monotonic()
        with self._lock:
            elapsed = now - self._last_backchannel_at
            if elapsed < self._min_interval_s:
                return False
            if self._this_turn_count >= self._max_per_turn:
                return False
            self._last_backchannel_at = now
            self._this_turn_count += 1

        # The hint gives conversational context but NOT the exact words.
        # Gemini Live generates a natural response from its current understanding.
        hint = context_hint or "acknowledge naturally if it helps the conversation"
        try:
            self._nudge_fn(hint)
        except Exception:  # noqa: BLE001
            return False
        return True

    def reset(self) -> None:
        with self._lock:
            self._last_backchannel_at = 0.0
            self._this_turn_count = 0
