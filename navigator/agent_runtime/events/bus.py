"""Synchronous event bus; Groq workers subscribe asynchronously."""

from __future__ import annotations

import threading
from collections.abc import Callable

from navigator.agent_runtime.models import AgentEvent


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: list[Callable[[AgentEvent], None]] = []
        self._log: list[AgentEvent] = []

    def subscribe(self, handler: Callable[[AgentEvent], None]) -> None:
        with self._lock:
            self._handlers.append(handler)

    def emit(self, event: AgentEvent) -> None:
        with self._lock:
            self._log.append(event)
            handlers = list(self._handlers)
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001
                print(f"[runtime] event handler failed: {exc}", flush=True)

    def history(self) -> list[AgentEvent]:
        with self._lock:
            return list(self._log)
