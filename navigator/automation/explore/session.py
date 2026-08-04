"""Exploration session state: budget, visited-state tracking, and the
question/answer rendezvous between the Playwright thread and the WebSocket.

State fingerprinting is by (url path + DOM structure hash), never URL alone.
An SPA changes state without changing the URL, and a paginated list links back
to itself with a different URL but an identical structure -- URL-only tracking
either misses states or loops forever on them.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from navigator.automation.explore.guardrail import FlaggedAction
from navigator.automation.record import RecordedStep

#: Session phases. `awaiting_input` genuinely blocks the explorer thread.
#: `starting` is set the moment the run is accepted so status.active is true
#: before the Playwright thread reaches logging_in / exploring.
PHASES = (
    "idle", "starting", "logging_in", "exploring", "awaiting_input",
    "drafting", "done", "failed", "stopped",
)


@dataclass(frozen=True)
class ExplorationBudget:
    """Bounds on a run. Defaults sized for a typical SaaS product surface."""

    max_pages: int = 25
    max_steps: int = 120
    max_wall_clock_s: float = 600.0
    #: Bounces / no-new-path clicks before early stop (after filtering visited nav).
    max_consecutive_no_new: int = 8
    #: Unanswered business-specific field → skip the field, keep exploring.
    answer_timeout_s: float = 300.0


@dataclass(frozen=True)
class StateFingerprint:
    url_path: str
    dom_hash: str

    def as_dict(self) -> dict[str, str]:
        return {"url_path": self.url_path, "dom_hash": self.dom_hash}


def fingerprint(url: str, elements: list[dict[str, Any]]) -> StateFingerprint:
    """Identity of a page *state*.

    Query and fragment are stripped: `?page=2` on a paginated list is the same
    explorable structure as `?page=3`, and treating them as distinct is exactly
    how a crawler runs forever.
    """
    path = urlparse(url or "").path or "/"
    sig = sorted(
        f"{e.get('role') or ''}|{e.get('tag') or ''}|"
        f"{e.get('testid') or ''}|{(e.get('text') or '')[:40]}"
        for e in elements
    )
    return StateFingerprint(path, hashlib.sha256("\n".join(sig).encode()).hexdigest()[:16])


def element_key(el: dict[str, Any]) -> str:
    """Stable identity for one interactive element within a state."""
    for attr in ("testid", "id", "name"):
        if el.get(attr):
            return f"{attr}={el[attr]}"
    return f"{el.get('tag') or 'el'}:{(el.get('text') or '')[:40]}"


@dataclass
class Question:
    qid: str
    alias: str
    prompt: str
    context: dict[str, Any] = field(default_factory=dict)
    answer: str | None = None
    skipped: bool = False
    timed_out: bool = False
    """Distinguishes "nobody was watching" from "the client chose to skip" in
    the post-run review -- the first is worth retrying, the second is not."""


@dataclass
class FieldDecision:
    """How one form field was filled, for the client's post-run review."""

    alias: str
    label: str
    classification: str  # guessable_safe | business_specific
    value: str
    answered_by: str  # auto | client | skipped_timeout | skipped_client

    def as_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "label": self.label,
            "classification": self.classification,
            "value": self.value,
            "answered_by": self.answered_by,
        }


@dataclass
class ExplorationSession:
    """Mutable state for one run, shared between explorer thread and WS handlers."""

    product_id: str
    base_url: str
    session_id: UUID = field(default_factory=uuid4)
    job_id: str = field(default_factory=lambda: str(uuid4()))
    phase: str = "idle"
    budget: ExplorationBudget = field(default_factory=ExplorationBudget)

    visited: dict[StateFingerprint, set[str]] = field(default_factory=dict)
    steps: list[RecordedStep] = field(default_factory=list)
    #: Every interaction attempted (budget). `steps` is the curated demo only.
    actions_taken: int = 0
    #: URL paths already represented in the demo flow (one entry click each).
    flow_paths: set[str] = field(default_factory=set)
    flagged: list[FlaggedAction] = field(default_factory=list)
    field_decisions: list[FieldDecision] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    pending_question: Question | None = None
    _answer_event: threading.Event = field(default_factory=threading.Event)
    stop_event: threading.Event = field(default_factory=threading.Event)

    error: str = ""
    flow_id: str = ""
    revision: int | None = None
    #: "new" → mint explored_* flow; "update" → overwrite target_flow_id.
    save_mode: str = "new"
    target_flow_id: str = ""
    target_flow_name: str = ""
    started_at: float = field(default_factory=time.monotonic)
    consecutive_no_new: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _listeners: list[Any] = field(default_factory=list)

    # -- budget ---------------------------------------------------------------

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def budget_exhausted(self) -> str | None:
        """Reason the run must stop, or None to keep going."""
        if self.stop_event.is_set():
            return "stopped by client"
        # Unique URL paths — SPA re-renders of the same path do not burn budget.
        unique_paths = len({fp.url_path for fp in self.visited})
        if unique_paths >= self.budget.max_pages:
            return f"max_pages ({self.budget.max_pages}) reached"
        # Budget against discovery actions, not curated demo length.
        if self.actions_taken >= self.budget.max_steps:
            return f"max_steps ({self.budget.max_steps}) reached"
        if self.elapsed_s() >= self.budget.max_wall_clock_s:
            return f"time budget ({self.budget.max_wall_clock_s:.0f}s) reached"
        if self.consecutive_no_new > self.budget.max_consecutive_no_new:
            return "no new interactive elements found"
        return None

    # -- visited-state bookkeeping -------------------------------------------

    def mark_visited(self, fp: StateFingerprint) -> bool:
        """Register a state. True if it had not been seen before."""
        if fp in self.visited:
            return False
        self.visited[fp] = set()
        return True

    def untried(
        self, fp: StateFingerprint, elements: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        tried = self.visited.setdefault(fp, set())
        return [e for e in elements if element_key(e) not in tried]

    def mark_tried(self, fp: StateFingerprint, el: dict[str, Any]) -> None:
        self.visited.setdefault(fp, set()).add(element_key(el))

    # -- event stream ---------------------------------------------------------

    def emit(self, event: dict[str, Any]) -> None:
        """Buffer an event and fan it out. Buffer replays to late WS joiners."""
        with self._lock:
            self.events.append(event)
            listeners = list(self._listeners)
        for q in listeners:
            try:
                q.put_nowait(event)
            except Exception:  # noqa: BLE001
                pass  # a full/closed listener queue must not stall exploration

    def add_listener(self, q: Any) -> list[dict[str, Any]]:
        with self._lock:
            self._listeners.append(q)
            return list(self.events)

    def remove_listener(self, q: Any) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def status(self) -> dict[str, Any]:
        # Same URL path can appear under multiple DOM hashes (SPA re-renders);
        # dashboard list should show unique paths, order preserved.
        visited_paths = list(dict.fromkeys(fp.url_path for fp in self.visited))
        # Last log-ish events for dashboard pollers that missed the WS stream.
        recent = [
            e
            for e in self.events
            if e.get("type") in {"log", "flagged", "field", "explored"}
        ][-80:]
        steps = len(self.steps)
        pages = len({fp.url_path for fp in self.visited})
        max_pages = max(1, self.budget.max_pages)
        # Progress = unique pages covered / budget. No soft time floor that
        # freezes the meter at 20% while the explorer is still crawling.
        progress = int(round(100 * pages / max_pages))
        progress = min(100, max(0, progress))
        active = self.phase not in {"done", "failed", "stopped", "idle"}
        if self.phase == "done" and progress < 100:
            progress = 100
        return {
            "active": active,
            "job_id": self.job_id,
            "session_id": str(self.session_id),
            "phase": self.phase,
            "visited": pages,
            "visited_paths": visited_paths,
            "steps": steps,
            "actions_taken": self.actions_taken,
            "flagged": [f.as_dict() for f in self.flagged],
            "field_decisions": [d.as_dict() for d in self.field_decisions],
            "recent_events": recent,
            "elapsed_s": round(self.elapsed_s(), 1),
            "progress_pct": progress,
            "budget": {
                "max_pages": self.budget.max_pages,
                "max_steps": self.budget.max_steps,
                "max_wall_clock_s": self.budget.max_wall_clock_s,
            },
            "save_mode": self.save_mode,
            "target_flow_id": self.target_flow_id or None,
            "target_flow_name": self.target_flow_name or None,
            "error": self.error,
            "flow_id": self.flow_id,
            "revision": self.revision,
            "pending_question": (
                {
                    "qid": self.pending_question.qid,
                    "alias": self.pending_question.alias,
                    "prompt": self.pending_question.prompt,
                    "context": self.pending_question.context,
                }
                if self.pending_question
                else None
            ),
        }

    # -- question rendezvous --------------------------------------------------

    def ask(self, alias: str, prompt: str, context: dict[str, Any]) -> Question:
        """Block the explorer thread until the client answers, or time out.

        Returns the Question with `answer` set, or `skipped` True on timeout.
        """
        q = Question(qid=f"q_{uuid4().hex[:12]}", alias=alias, prompt=prompt, context=context)
        self.pending_question = q
        self._answer_event.clear()
        prev_phase = self.phase
        self.phase = "awaiting_input"
        self.emit(
            {"type": "question", "qid": q.qid, "alias": alias,
             "prompt": prompt, "context": context}
        )

        answered = self._answer_event.wait(timeout=self.budget.answer_timeout_s)
        if not answered and not q.skipped:
            q.skipped = True
            q.timed_out = True
            self.emit(
                {"type": "log", "level": "warn",
                 "msg": f"no answer for {alias!r} within "
                        f"{self.budget.answer_timeout_s:.0f}s — skipping field"}
            )
        self.pending_question = None
        # A client "stop" during the wait must not be overwritten back to exploring.
        self.phase = "stopped" if self.stop_event.is_set() else prev_phase
        return q

    def answer(self, qid: str, value: str) -> bool:
        """Resolve a pending question. False if qid does not match.

        The qid check matters: a reconnected dashboard tab can post a stale
        answer, and applying it to whatever question happens to be open now
        would silently fill the wrong field.
        """
        q = self.pending_question
        if q is None or q.qid != qid:
            return False
        q.answer = value
        self._answer_event.set()
        return True

    def skip_question(self, qid: str) -> bool:
        q = self.pending_question
        if q is None or q.qid != qid:
            return False
        q.skipped = True
        self._answer_event.set()
        return True

    def request_stop(self) -> None:
        self.stop_event.set()
        self._answer_event.set()  # unblock a parked ask()
        # Flip phase immediately so dashboard status.active goes false without
        # waiting for the Playwright/LLM thread to notice the event.
        if self.phase not in {"done", "failed", "stopped"}:
            self.phase = "stopped"
        self.emit(
            {"type": "log", "level": "info", "msg": "stop requested — winding down"}
        )
        self.emit({"type": "status", **self.status()})
