"""Event sink protocol for async Groq enrichment."""

from __future__ import annotations

from typing import Protocol

from navigator.agent_runtime.models import AgentEvent


class EventSink(Protocol):
    def emit(self, event: AgentEvent) -> None:
        ...
