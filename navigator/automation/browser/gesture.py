"""Whether the current tool attempt already sent a click or a fill.

Retries must not send the gesture again after it landed and only the
confirmation timed out. The flag lives on the attempt thread.
"""

from __future__ import annotations

import threading

_local = threading.local()


def begin_attempt() -> None:
    _local.sent = False


def note_gesture_sent() -> None:
    _local.sent = True


def gesture_was_sent() -> bool:
    return bool(getattr(_local, "sent", False))
