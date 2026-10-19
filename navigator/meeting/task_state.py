"""Single owner of "the prospect's active task".

While a task is active (pending / executing / awaiting_user) the scripted flow,
planner, recovery, orchestrator and narrated actions must not move the screen
or speak over it. 10:33 run: during a Forgot Password task the flow resumed
after 98.6s, jumped to Campaigns and narrated while the prospect was still
giving their email.

Every navigation asks ``nav_allowed(owner, reason, task_id)``; the decision is
logged as ``[nav] owner=.. reason=.. task=.. allowed|blocked``.
"""
from __future__ import annotations

import itertools
import re
import threading
import time
from dataclasses import dataclass, field

ACTIVE_STATES = {"pending", "executing", "awaiting_user"}
TERMINAL_STATES = {"completed", "cancelled", "failed"}
#: owners that act FOR the active task (the prospect's own requests)
TASK_OWNERS = {"task", "user"}

_ids = itertools.count(1)
_lock = threading.RLock()


@dataclass
class Task:
    id: str
    goal: str
    heard: str = ""
    state: str = "pending"
    created: float = field(default_factory=time.monotonic)
    updated: float = field(default_factory=time.monotonic)
    screen: str = ""
    history: list = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES


CURRENT: Task | None = None


def _log(msg: str) -> None:
    print(f"[task] {msg}", flush=True)


def current() -> Task | None:
    return CURRENT


def active() -> Task | None:
    t = CURRENT
    return t if t is not None and t.active else None


def active_id() -> str:
    t = active()
    return t.id if t else ""


def start(goal: str, heard: str = "") -> Task:
    """A new prospect instruction: supersedes (cancels) any active task."""
    global CURRENT
    with _lock:
        old = CURRENT
        if old is not None and old.active:
            transition(old.id, "cancelled", "superseded by a new instruction")
        CURRENT = Task(id=f"t{next(_ids)}", goal=(goal or "")[:120], heard=heard)
        _log(f"id={CURRENT.id} state=pending goal={CURRENT.goal!r}")
        return CURRENT


def transition(task_id: str, state: str, why: str = "", screen: str = "") -> bool:
    with _lock:
        t = CURRENT
        if t is None or t.id != task_id:
            return False
        if t.state in TERMINAL_STATES:
            return False
        prev, t.state, t.updated = t.state, state, time.monotonic()
        if screen:
            t.screen = screen
        t.history.append((t.updated, prev, state, why))
        _log(f"id={t.id} {prev} -> {state}{f' ({why})' if why else ''}{f' screen={screen}' if screen else ''}")
        return True


def finish_active(state: str = "completed", why: str = "") -> bool:
    t = active()
    return bool(t and transition(t.id, state, why))


def is_stale(task_id: str) -> bool:
    """A queued action whose task was superseded/cancelled must not run."""
    if not task_id:
        return False
    t = CURRENT
    return t is None or t.id != task_id or t.state in {"cancelled", "failed"}


def nav_allowed(owner: str, reason: str, task_id: str = "") -> bool:
    t = active()
    if t is None:
        ok = True
    elif owner in TASK_OWNERS:
        ok = not task_id or task_id == t.id
    else:
        ok = False
    _log_nav(owner, reason, t.id if t else "-", ok)
    return ok


def _log_nav(owner: str, reason: str, tid: str, ok: bool) -> None:
    print(f"[nav] owner={owner} reason={reason!r} task={tid} {'allowed' if ok else 'blocked'}", flush=True)


_CONTINUE = re.compile(
    r"\b(continue|carry on|move on|go on|resume|next (step|part|feature)|keep going|back to the demo|"
    r"proceed|that'?s (all|it|done)|i'?m done|we'?re done|got it,? thanks|thank you,? (that'?s|next))\b", re.I)
_CANCEL = re.compile(r"\b(never ?mind|forget it|cancel( that)?|stop (this|that)|leave it|skip (it|this))\b", re.I)


def on_heard(text: str) -> None:
    """Prospect speech: 'continue/move on' completes, 'never mind' cancels the
    active task; anything else while awaiting_user keeps it alive."""
    t = active()
    if t is None:
        return
    if _CANCEL.search(text or ""):
        transition(t.id, "cancelled", f"prospect: {text[:40]!r}")
    elif _CONTINUE.search(text or ""):
        transition(t.id, "completed", f"prospect: {text[:40]!r}")
    else:
        t.updated = time.monotonic()


def ended_by(text: str) -> bool:
    """This utterance closed the last task ('continue the demo'): a tool call
    answering it must not open a new task."""
    t = CURRENT
    return bool(text and t is not None and t.state in TERMINAL_STATES and t.history
                and f"prospect: {text[:40]!r}" == t.history[-1][3])


def idle_s(now: float | None = None) -> float:
    t = active()
    return 0.0 if t is None else (time.monotonic() if now is None else now) - t.updated


def reset() -> None:
    global CURRENT
    with _lock:
        CURRENT = None
