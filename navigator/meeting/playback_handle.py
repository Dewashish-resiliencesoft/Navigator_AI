"""Async Meet TTS handle — overlap narration with browser actions."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class PlaybackHandle:
    """Background TTS job started by MeetSpeaker.say_async."""

    _cancel: threading.Event = field(default_factory=threading.Event)
    _done: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    error: str | None = None

    def wait(self, timeout: float | None = None) -> None:
        self._done.wait(timeout)

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _finish(self) -> None:
        self._done.set()
