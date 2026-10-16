"""Authoritative in-process world state with versioned updates."""

from __future__ import annotations

import copy
import threading
from typing import Callable

from navigator.agent_runtime.models import AgentWorldState, utc_now


class WorldStateStore:
    def __init__(self, initial: AgentWorldState) -> None:
        self._lock = threading.RLock()
        self._state = initial

    @property
    def state(self) -> AgentWorldState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def version(self) -> int:
        with self._lock:
            return self._state.version

    def update(self, reducer: Callable[[AgentWorldState], AgentWorldState]) -> AgentWorldState:
        with self._lock:
            next_state = reducer(copy.deepcopy(self._state))
            next_state.version = self._state.version + 1
            next_state.updated_at = utc_now()
            self._state = next_state
            return self._state.model_copy(deep=True)

    def replace(self, state: AgentWorldState) -> None:
        with self._lock:
            self._state = state
