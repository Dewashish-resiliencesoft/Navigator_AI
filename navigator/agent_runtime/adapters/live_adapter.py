"""Gemini Live realtime interface adapter.

Speech semantics
─────────────────
acknowledge(hint)   → immediate "I'm on it" ack — fired BEFORE Flash starts.
                      Gemini Live dynamically rephrases the hint naturally.
                      Keep hints short (≤ 10 words).

speak_result(text)  → deliver the completed task result / narration.
                      Called AFTER verification confirms success.
                      Never claim completion before verification.

speak_error(text)   → recovery or failure message.
                      Used for retries and unrecoverable failures.

push_world_state()  → lightweight JSON nudge so Live has current page context.
push_dom_context()  → full DOM snapshot (for Flash; Live gets the lightweight path).

Do NOT call acknowledge() to deliver results.
Do NOT call speak_result() before the task is verified.
"""

from __future__ import annotations

from typing import Any

from navigator.agent_runtime.dom.builder import build_dom_state


class LiveAdapter:
    """Pushes context and speech cues to the Gemini Live session."""

    def __init__(self, live_agent: Any) -> None:
        self._live = live_agent

    # ── speech paths ──────────────────────────────────────────────────────

    def acknowledge(self, hint: str) -> None:
        """Short "I'm working on it" — emitted immediately, before Flash starts."""
        if self._live is None:
            return
        say = getattr(self._live, "say", None)
        if callable(say):
            say(hint, mode="acknowledge")

    def speak_result(self, text: str) -> None:
        """Deliver task result or step narration — only after verification."""
        if self._live is None:
            return
        say = getattr(self._live, "say", None)
        if callable(say):
            say(text, mode="result")

    def speak_error(self, text: str) -> None:
        """Recovery or failure message — keep technical details out of this text."""
        if self._live is None:
            return
        say = getattr(self._live, "say", None)
        if callable(say):
            say(text, mode="error")

    # ── context / state nudges ────────────────────────────────────────────

    def push_dom_context(self, page: Any, *, page_id: str) -> None:
        """Full DOM snapshot for Flash reasoning (not for Live voice path)."""
        if self._live is None:
            return
        ctx = build_dom_state(page, page_id=page_id, detailed=False)
        nudge = getattr(self._live, "nudge", None)
        if callable(nudge):
            nudge(f"Current page context: {ctx}")

    def push_world_state(
        self,
        *,
        page: str = "",
        url: str = "",
        task_status: str = "",
        task_goal: str = "",
        browser_ready: bool = True,
    ) -> None:
        """Push a compact one-line world-state hint so Live has current context.

        Intentionally lightweight — Live gets page identity and task status,
        not full DOM.  Flash gets full DOM when it needs to plan.
        """
        if self._live is None:
            return
        parts: list[str] = []
        if page:
            parts.append(f"page={page}")
        if url:
            parts.append(f"url={url}")
        if task_status:
            parts.append(f"task={task_status}")
        if task_goal:
            parts.append(f"goal={task_goal[:60]}")
        if not browser_ready:
            parts.append("browser=loading")
        line = " | ".join(parts) if parts else "ready"
        nudge = getattr(self._live, "nudge", None)
        if callable(nudge):
            nudge(f"[state] {line}")

    def push_state_context(self, context_line: str) -> None:
        """Single-line realtime-state hint (LISTENING/WORKING/etc.)."""
        if self._live is None:
            return
        nudge = getattr(self._live, "nudge", None)
        if callable(nudge):
            nudge(f"[agent] {context_line}")

    # ── availability ──────────────────────────────────────────────────────

    def is_available(self) -> bool:
        return self._live is not None
