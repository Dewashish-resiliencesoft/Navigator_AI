"""DecisionTrace: why the agent did what it did, one row per live turn.

ActionLog answers "what did the browser do". This answers the question that
actually comes up after a bad demo: given what the prospect said, why did the
agent run that flow instead of answering, or answer instead of running?

Every live turn writes exactly one row -- including the boring ones. A trace with
gaps is worse than no trace, because the missing turns are indistinguishable from
turns that never happened.

Same SQLite conventions as ActionLog: one connection per thread (a demo runs on
its own thread while API handlers read), WAL so reads don't block the demo, and
product_id on every row and every read.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

#: Branch vocabulary. Documented rather than enforced -- a new branch in a later
#: phase should be one constant here, not a migration.
#:
#: continuation       -- silence or "ok, go on"; default flow advanced one step
#: flow_executed      -- a matched flow was run (default or a Topic detour)
#: knowledge_only     -- answered from knowledge, no tool calls
#: clarifying_question-- medium confidence; asked before doing anything
#: live_input         -- paused on a requires_live_input field and asked
#: handoff            -- nothing relevant found, escalated to a human
#: tier2_attempted    -- constrained live fallback ran an action
#: tier2_refused      -- constrained live fallback was blocked by the guardrail
#: correction         -- prospect corrected the agent; logged for review
#: ended              -- goodbye / wrap-up
BRANCHES = frozenset(
    {
        "continuation",
        "flow_executed",
        "knowledge_only",
        "clarifying_question",
        "live_input",
        "handoff",
        "tier2_attempted",
        "tier2_refused",
        "correction",
        "ended",
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_trace (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    product_id      TEXT NOT NULL,
    utterance       TEXT NOT NULL,   -- verbatim; empty means silence
    branch          TEXT NOT NULL,
    chosen_flow_id  TEXT,            -- NULL for non-flow branches
    spoken          TEXT NOT NULL,
    flow_candidates TEXT NOT NULL,   -- JSON [[flow_id, confidence], ...]
    knowledge_hits  TEXT NOT NULL,   -- JSON [[chunk_id, score], ...]
    detail          TEXT NOT NULL,   -- why this branch, in one line
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS decision_trace_session
    ON decision_trace (session_id, created_at);
CREATE INDEX IF NOT EXISTS decision_trace_product
    ON decision_trace (product_id, created_at);
"""


@dataclass(frozen=True)
class DecisionTrace:
    """One turn's decision, as recorded."""

    id: str
    session_id: str
    product_id: str
    utterance: str
    branch: str
    spoken: str
    created_at: str
    chosen_flow_id: str | None = None
    flow_candidates: tuple[tuple[str, float], ...] = ()
    knowledge_hits: tuple[tuple[str, float], ...] = ()
    detail: str = ""

    @property
    def was_silent(self) -> bool:
        return not self.utterance.strip()

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "product_id": self.product_id,
            "utterance": self.utterance,
            "branch": self.branch,
            "chosen_flow_id": self.chosen_flow_id,
            "spoken": self.spoken,
            "flow_candidates": [list(c) for c in self.flow_candidates],
            "knowledge_hits": [list(h) for h in self.knowledge_hits],
            "detail": self.detail,
            "created_at": self.created_at,
        }


class DecisionTraceStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if self.db_path.parent != Path():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conn.executescript(_SCHEMA)

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    # -- write ---------------------------------------------------------------

    def record(
        self,
        *,
        product_id: str,
        session_id: UUID | str,
        utterance: str,
        branch: str,
        spoken: str,
        chosen_flow_id: str | None = None,
        flow_candidates: Sequence[tuple[str, float]] = (),
        knowledge_hits: Sequence[tuple[str, float]] = (),
        detail: str = "",
    ) -> DecisionTrace:
        """Append one turn's decision.

        Never raises on an unknown `branch`: a trace is diagnostic, and losing the
        record of a turn because a later phase added a branch string is a worse
        failure than a row with an unfamiliar label.
        """
        row = DecisionTrace(
            id=str(uuid4()),
            session_id=str(session_id),
            product_id=product_id,
            utterance=utterance,
            branch=branch,
            spoken=spoken,
            created_at=datetime.now(timezone.utc).isoformat(),
            chosen_flow_id=chosen_flow_id,
            flow_candidates=tuple((str(f), float(c)) for f, c in flow_candidates),
            knowledge_hits=tuple((str(k), float(s)) for k, s in knowledge_hits),
            detail=detail,
        )
        self._conn.execute(
            """
            INSERT INTO decision_trace (
                id, session_id, product_id, utterance, branch, chosen_flow_id,
                spoken, flow_candidates, knowledge_hits, detail, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row.id,
                row.session_id,
                row.product_id,
                row.utterance,
                row.branch,
                row.chosen_flow_id,
                row.spoken,
                json.dumps([list(c) for c in row.flow_candidates]),
                json.dumps([list(h) for h in row.knowledge_hits]),
                row.detail,
                row.created_at,
            ),
        )
        return row

    # -- read ----------------------------------------------------------------

    def for_session(
        self, session_id: UUID | str, product_id: str | None = None
    ) -> list[DecisionTrace]:
        """One call's decisions, oldest first -- the post-mortem read.

        `product_id` is defence in depth, matching ActionLog.entries: session ids
        are unguessable UUIDs, but a multi-tenant read should not rely on that.
        """
        sql = "SELECT * FROM decision_trace WHERE session_id = ?"
        params: tuple = (str(session_id),)
        if product_id is not None:
            sql += " AND product_id = ?"
            params += (product_id,)
        return self._query(sql + " ORDER BY created_at, rowid", params)

    def for_product(
        self, product_id: str, *, branch: str | None = None, limit: int = 200
    ) -> list[DecisionTrace]:
        """Recent decisions across one product's calls, newest first."""
        sql = "SELECT * FROM decision_trace WHERE product_id = ?"
        params: tuple = (product_id,)
        if branch is not None:
            sql += " AND branch = ?"
            params += (branch,)
        return self._query(
            sql + " ORDER BY created_at DESC, rowid DESC LIMIT ?", params + (limit,)
        )

    def _query(self, sql: str, params: tuple) -> list[DecisionTrace]:
        return [self._row(r) for r in self._conn.execute(sql, params).fetchall()]

    @staticmethod
    def _row(r: sqlite3.Row) -> DecisionTrace:
        return DecisionTrace(
            id=r["id"],
            session_id=r["session_id"],
            product_id=r["product_id"],
            utterance=r["utterance"],
            branch=r["branch"],
            spoken=r["spoken"],
            created_at=r["created_at"],
            chosen_flow_id=r["chosen_flow_id"],
            flow_candidates=tuple(
                (str(f), float(c)) for f, c in json.loads(r["flow_candidates"])
            ),
            knowledge_hits=tuple(
                (str(k), float(s)) for k, s in json.loads(r["knowledge_hits"])
            ),
            detail=r["detail"],
        )

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def __enter__(self) -> DecisionTraceStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
