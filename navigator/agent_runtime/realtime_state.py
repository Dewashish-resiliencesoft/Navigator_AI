"""Explicit realtime agent states — Gemini Live always knows current state.

The state machine is owned by RealtimeController, which is the single source of
truth for what the agent is doing right now. Gemini Live receives compact
context updates whenever the state changes so it can decide what to say without
needing to query the orchestrator synchronously.

State transition rules (enforced by RealtimeController):
  LISTENING → UNDERSTANDING (utterance received)
  UNDERSTANDING → BACKCHANNELING | RESPONDING | DELEGATING
  BACKCHANNELING → LISTENING
  RESPONDING → LISTENING
  DELEGATING → WORKING (task handed to orchestrator)
  WORKING → RESPONDING (result ready)
  WORKING → RECOVERING (task failed, retrying)
  RECOVERING → WORKING | RESPONDING | FINISHING
  FINISHING → LISTENING | end
  FAILED → FINISHING
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Callable


class RealtimeState(str, Enum):
    LISTENING = "listening"
    UNDERSTANDING = "understanding"
    BACKCHANNELING = "backchanneling"
    RESPONDING = "responding"
    DELEGATING = "delegating"
    WORKING = "working"
    WAITING_FOR_USER = "waiting_for_user"
    RECOVERING = "recovering"
    FINISHING = "finishing"
    FAILED = "failed"


# Lightweight context pushed to Gemini Live when state changes.
_STATE_CONTEXT: dict[RealtimeState, str] = {
    RealtimeState.LISTENING: "[State: listening — wait for the user to speak]",
    RealtimeState.UNDERSTANDING: "[State: processing what the user said]",
    RealtimeState.BACKCHANNELING: "[State: user still speaking — stay brief]",
    RealtimeState.RESPONDING: "[State: answering the user]",
    RealtimeState.DELEGATING: "[State: handing task to the browser — give a brief natural ack, then go quiet]",
    RealtimeState.WORKING: "[State: browser is working on the task — stay present but don't claim it's done yet]",
    RealtimeState.WAITING_FOR_USER: "[State: waiting for user input]",
    RealtimeState.RECOVERING: "[State: retrying after a small issue — mention it naturally if the user asks]",
    RealtimeState.FINISHING: "[State: task complete — give a natural summary]",
    RealtimeState.FAILED: "[State: encountered an issue — apologise and offer handoff]",
}


class RealtimeController:
    """Single source of truth for what the voice agent is doing right now.

    Thread-safe. All transitions go through ``transition()`` so listeners
    (Gemini Live context feed, logging) always see consistent state.
    """

    def __init__(
        self,
        *,
        on_state_change: Callable[[RealtimeState, RealtimeState], None] | None = None,
        live_agent: object | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._state = RealtimeState.LISTENING
        self._task_goal: str = ""
        self._task_start: float = 0.0
        self._on_change = on_state_change
        self._live = live_agent

    @property
    def state(self) -> RealtimeState:
        with self._lock:
            return self._state

    @property
    def is_working(self) -> bool:
        with self._lock:
            return self._state in (
                RealtimeState.WORKING,
                RealtimeState.DELEGATING,
                RealtimeState.RECOVERING,
            )

    @property
    def task_elapsed_s(self) -> float:
        with self._lock:
            if self._task_start == 0.0:
                return 0.0
            return time.monotonic() - self._task_start

    def transition(self, new_state: RealtimeState, *, goal: str = "") -> None:
        with self._lock:
            old = self._state
            if old == new_state:
                return
            self._state = new_state
            if goal:
                self._task_goal = goal
            if new_state in (RealtimeState.WORKING, RealtimeState.DELEGATING):
                self._task_start = time.monotonic()
            elif new_state not in (RealtimeState.RECOVERING,):
                self._task_start = 0.0

        # Push compact state context to Live so it knows without calling back
        ctx_line = _STATE_CONTEXT.get(new_state, "")
        if ctx_line and self._live is not None:
            add_ctx = getattr(self._live, "add_context", None)
            if callable(add_ctx):
                try:
                    add_ctx(ctx_line)
                except Exception:  # noqa: BLE001
                    pass

        if self._on_change is not None:
            try:
                self._on_change(old, new_state)
            except Exception:  # noqa: BLE001
                pass

    def attach_live(self, live_agent: object) -> None:
        with self._lock:
            self._live = live_agent

    def compact_context(self) -> dict:
        """Lightweight context dict for Live system prompt injection."""
        with self._lock:
            return {
                "agent_state": self._state.value,
                "task_goal": self._task_goal,
                "task_elapsed_s": round(self.task_elapsed_s, 1),
            }
