"""Gemini Live realtime interface adapter.

Owns the boundary between the orchestrator/Live; exposes:
  - acknowledge()       → immediate natural ack while Flash starts
  - push_dom_context()  → compact page context (NOT raw DOM)
  - push_world_state()  → lightweight world-state JSON for Live context
  - nudge_progress()    → forward a task-progress hint for Live to narrate
  - is_available()      → guard
"""

from __future__ import annotations

import json
from typing import Any

from navigator.agent_runtime.dom.builder import build_dom_state


class LiveAdapter:
    """Push compact context into Live; route acks and progress hints."""

    def __init__(self, live_agent: Any) -> None:
        self._live = live_agent

    # ---- speaking -------------------------------------------------------

    def acknowledge(self, task_hint: str) -> None:
        """Emit an immediate natural acknowledgement without hardcoded phrasing.

        ``task_hint`` is the user's goal — Live generates the words from it.
        Never called after the task is completed; this is the *immediate* ack.
        """
        if not self.is_available():
            return
        nudge = getattr(self._live, "nudge", None)
        if callable(nudge):
            nudge(
                f"The user just asked: '{task_hint}'. "
                "Give ONE brief, natural acknowledgement that you're on it, then go quiet. "
                "Do NOT claim it is done."
            )

    def nudge_progress(self, context_hint: str) -> None:
        """Forward an orchestrator progress hint; Live decides whether to speak."""
        if not self.is_available():
            return
        nudge = getattr(self._live, "nudge", None)
        if callable(nudge):
            nudge(context_hint)

    # ---- context --------------------------------------------------------

    def push_dom_context(self, page: Any, *, page_id: str) -> None:
        """Push compact page context (not raw DOM) for Live awareness."""
        if not self.is_available():
            return
        ctx = build_dom_state(page, page_id=page_id, detailed=False)
        add_ctx = getattr(self._live, "add_context", None)
        if callable(add_ctx):
            add_ctx(f"[Page context — do not read aloud] {ctx}")

    def push_world_state(
        self,
        *,
        page: str = "",
        url: str = "",
        task_status: str = "",
        task_goal: str = "",
        browser_ready: bool = True,
    ) -> None:
        """Push a lightweight world-state snapshot to Live.

        Live uses this to give contextually appropriate responses.
        The full DOM/screenshot/history stay with Flash.
        """
        if not self.is_available():
            return
        payload = {
            "page": page,
            "url": url,
            "task": {"status": task_status, "goal": task_goal},
            "browser": {"ready": browser_ready},
        }
        add_ctx = getattr(self._live, "add_context", None)
        if callable(add_ctx):
            add_ctx(f"[World state — do not read aloud] {json.dumps(payload)}")

    def push_state_context(self, context_line: str) -> None:
        """Inject a single-line agent-state hint."""
        if not self.is_available():
            return
        add_ctx = getattr(self._live, "add_context", None)
        if callable(add_ctx):
            add_ctx(context_line)

    def is_available(self) -> bool:
        return self._live is not None
