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
        # Keep servicing Live screen-tool jobs while the demo thread waits.
        import time as _t

        from navigator.meeting import ui_jobs

        end = None if timeout is None else _t.monotonic() + timeout
        while not self._done.wait(0.05):
            ui = ui_jobs.CURRENT
            if ui is not None:
                ui.pump()
            if end is not None and _t.monotonic() >= end:
                return

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _finish(self) -> None:
        self._done.set()
