"""Pending correction review table (SQLite). Never auto-promoted to Chroma."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_corrections (
    id           TEXT PRIMARY KEY,
    product_id   TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    page         TEXT NOT NULL,
    tool_call_type TEXT NOT NULL,
    rule         TEXT NOT NULL,
    source_call_id TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS pending_corr_product
    ON pending_corrections (product_id, status, created_at);
"""


@dataclass(frozen=True)
class PendingCorrection:
    id: str
    product_id: str
    session_id: str
    page: str
    tool_call_type: str
    rule: str
    source_call_id: str
    created_at: str
    status: str = "pending"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "product_id": self.product_id,
            "session_id": self.session_id,
            "page": self.page,
            "tool_call_type": self.tool_call_type,
            "rule": self.rule,
            "source_call_id": self.source_call_id,
            "created_at": self.created_at,
            "status": self.status,
        }


class PendingCorrectionStore:
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
            self._local.conn = conn
        return conn

    def add(
        self,
        *,
        product_id: str,
        session_id: UUID | str,
        page: str,
        tool_call_type: str,
        rule: str,
        source_call_id: UUID | str,
    ) -> PendingCorrection:
        row = PendingCorrection(
            id=str(uuid4()),
            product_id=product_id,
            session_id=str(session_id),
            page=page,
            tool_call_type=tool_call_type,
            rule=rule.strip(),
            source_call_id=str(source_call_id),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._conn.execute(
            """
            INSERT INTO pending_corrections
            (id, product_id, session_id, page, tool_call_type, rule,
             source_call_id, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.id,
                row.product_id,
                row.session_id,
                row.page,
                row.tool_call_type,
                row.rule,
                row.source_call_id,
                row.created_at,
                row.status,
            ),
        )
        return row

    def list_pending(self, product_id: str, *, limit: int = 50) -> list[PendingCorrection]:
        cur = self._conn.execute(
            """
            SELECT * FROM pending_corrections
            WHERE product_id = ? AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (product_id, limit),
        )
        return [self._row(r) for r in cur.fetchall()]

    @staticmethod
    def _row(r: sqlite3.Row) -> PendingCorrection:
        return PendingCorrection(
            id=r["id"],
            product_id=r["product_id"],
            session_id=r["session_id"],
            page=r["page"],
            tool_call_type=r["tool_call_type"],
            rule=r["rule"],
            source_call_id=r["source_call_id"],
            created_at=r["created_at"],
            status=r["status"],
        )

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def __enter__(self) -> PendingCorrectionStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
