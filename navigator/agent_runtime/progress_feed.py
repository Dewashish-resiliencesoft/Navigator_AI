"""Progress event feed — translates orchestrator events into Gemini Live nudges.

The model decides what to say; this module decides WHEN to speak and
what context hint to provide. No hardcoded phrases.

Events that carry user-visible meaning trigger a nudge with a context
hint describing what just happened. Gemini Live then chooses a natural
response or stays silent.

Long-running tasks (> progress_threshold_s) trigger a periodic
"still working" nudge — again, wording chosen by the model from context.
"""

from __future__ import annotations

import time
import threading
from typing import Callable

from navigator.agent_runtime.task_delegate import TaskEvent, TaskStatus

# Events that may warrant a user-facing progress update
_NOTABLE_EVENTS = frozenset({
    TaskStatus.PLAN_READY,
    TaskStatus.ACTION_STARTED,
    TaskStatus.ACTION_COMPLETED,
    TaskStatus.VERIFICATION_COMPLETE,
    TaskStatus.RECOVERY_STARTED,
    TaskStatus.TASK_COMPLETED,
    TaskStatus.TASK_FAILED,
})

# Natural context hints per status — the model generates the actual words
_CONTEXT_HINTS: dict[TaskStatus, str] = {
    TaskStatus.PLAN_READY: "You have a plan ready; optionally mention you're starting",
    TaskStatus.ACTION_STARTED: "A browser action just started; only speak if asked",
    TaskStatus.ACTION_COMPLETED: "A step completed; you can mention progress briefly if natural",
    TaskStatus.VERIFICATION_COMPLETE: "The step was verified; continue naturally",
    TaskStatus.RECOVERY_STARTED: "Hit a small issue, trying another way; mention casually if relevant",
    TaskStatus.TASK_COMPLETED: "Task is done; give a natural summary of what was found/done",
    TaskStatus.TASK_FAILED: "Task failed despite retries; apologise naturally and offer handoff",
}


class ProgressFeed:
    """Forward task events to Gemini Live with rate limiting.

    Args:
        nudge_fn: Sends a nudge hint to Gemini Live.
        min_event_interval_s: Minimum seconds between progress nudges.
        progress_threshold_s: Emit a "still working" nudge after this many
                              seconds of silence on a running task.
    """

    def __init__(
        self,
        *,
        nudge_fn: Callable[[str], None] | None = None,
        min_event_interval_s: float = 4.0,
        progress_threshold_s: float = 8.0,
    ) -> None:
        self._nudge = nudge_fn
        self._min_interval = min_event_interval_s
        self._progress_threshold = progress_threshold_s

        self._lock = threading.Lock()
        self._last_nudge_at: float = 0.0
        self._task_start_at: float = 0.0
        self._last_nudge_for: str = ""

    def on_task_event(self, event: TaskEvent) -> None:
        """Called by TaskDelegator.on_event_fn on each orchestrator event."""
        if event.status == TaskStatus.CREATED:
            with self._lock:
                self._task_start_at = time.monotonic()
                self._last_nudge_at = time.monotonic()
            return

        if event.status not in _NOTABLE_EVENTS:
            return

        hint = _CONTEXT_HINTS.get(event.status, "")
        if event.detail:
            hint = f"{hint}; detail: {event.detail}"

        self._maybe_nudge(hint)

    def maybe_progress_nudge(self, *, goal: str = "") -> None:
        """Call periodically while a task is running.

        Emits a "still working" hint if enough time has passed since the last
        nudge and the task has been running above the progress threshold.
        """
        with self._lock:
            if self._task_start_at == 0.0:
                return
            elapsed = time.monotonic() - self._task_start_at
            if elapsed < self._progress_threshold:
                return

        hint = f"You are still working on: {goal or 'the task'}. Say something natural if silence has been too long."
        self._maybe_nudge(hint)

    def reset(self) -> None:
        with self._lock:
            self._task_start_at = 0.0
            self._last_nudge_at = 0.0

    def _maybe_nudge(self, hint: str) -> None:
        if not self._nudge or not hint:
            return
        now = time.monotonic()
        with self._lock:
            if now - self._last_nudge_at < self._min_interval:
                return
            self._last_nudge_at = now

        try:
            self._nudge(hint)
        except Exception:  # noqa: BLE001
            pass
