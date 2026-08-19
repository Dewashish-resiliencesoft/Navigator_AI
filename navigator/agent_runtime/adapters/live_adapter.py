"""Gemini Live realtime interface adapter."""

from __future__ import annotations

from typing import Any

from navigator.agent_runtime.dom.builder import build_dom_state


class LiveAdapter:
    """Push compact DOM context into Live; speak acks while Flash thinks."""

    def __init__(self, live_agent: Any) -> None:
        self._live = live_agent

    def acknowledge(self, text: str) -> None:
        if self._live is None:
            return
        say = getattr(self._live, "say", None)
        if callable(say):
            say(text, mode="natural")

    def push_dom_context(self, page: Any, *, page_id: str) -> None:
        if self._live is None:
            return
        ctx = build_dom_state(page, page_id=page_id, detailed=False)
        nudge = getattr(self._live, "nudge", None)
        if callable(nudge):
            nudge(f"Current page context: {ctx}")

    def is_available(self) -> bool:
        return self._live is not None
