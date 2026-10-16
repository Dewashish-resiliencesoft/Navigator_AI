"""Meeting transcript: screen + speech turns for one demo run.

ActionLog answers what the browser did. This answers what happened in the
meeting for the Client: which screen, what the agent said, what the visitor
asked, and how the agent replied.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

KINDS = frozenset({"screen", "agent", "visitor", "agent_reply"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meeting_transcript (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    product_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    page_id     TEXT NOT NULL,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS meeting_transcript_session
    ON meeting_transcript (session_id, created_at);
CREATE INDEX IF NOT EXISTS meeting_transcript_product
    ON meeting_transcript (product_id, created_at);
"""


@dataclass(frozen=True)
class MeetingTranscriptEntry:
    id: str
    session_id: str
    product_id: str
    kind: str
    page_id: str
    text: str
    created_at: str


class MeetingTranscriptStore:
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

    def append(
        self,
        *,
        product_id: str,
        session_id: UUID | str,
        kind: str,
        page_id: str = "",
        text: str,
    ) -> MeetingTranscriptEntry | None:
        if kind not in KINDS:
            raise ValueError(f"unknown transcript kind: {kind!r}")
        body = (text or "").strip()
        if not body:
            return None
        row = MeetingTranscriptEntry(
            id=str(uuid4()),
            session_id=str(session_id),
            product_id=product_id,
            kind=kind,
            page_id=(page_id or "").strip(),
            text=body,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._conn.execute(
            """
            INSERT INTO meeting_transcript
                (id, session_id, product_id, kind, page_id, text, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                row.id,
                row.session_id,
                row.product_id,
                row.kind,
                row.page_id,
                row.text,
                row.created_at,
            ),
        )
        return row

    def for_session(
        self, session_id: UUID | str, product_id: str | None = None
    ) -> list[MeetingTranscriptEntry]:
        sql = "SELECT * FROM meeting_transcript WHERE session_id = ?"
        params: tuple = (str(session_id),)
        if product_id is not None:
            sql += " AND product_id = ?"
            params += (product_id,)
        rows = self._conn.execute(
            sql + " ORDER BY created_at, rowid", params
        ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r: sqlite3.Row) -> MeetingTranscriptEntry:
        return MeetingTranscriptEntry(
            id=r["id"],
            session_id=r["session_id"],
            product_id=r["product_id"],
            kind=r["kind"],
            page_id=r["page_id"],
            text=r["text"],
            created_at=r["created_at"],
        )

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def __enter__(self) -> MeetingTranscriptStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
