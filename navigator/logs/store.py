"""ActionLog storage.

Every tool call the agent makes lands here with what it expected and what actually
happened. This is the table REFLECTING reads from, and the thing that makes a call
debuggable after the fact.

SQLite via stdlib for Phase 1 -- append-only writes plus a couple of indexed
reads. Postgres arrives with docker-compose in a later phase; the swap is confined
to this file because everything above it speaks ActionLogEntry.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from navigator.core.schemas import ActionLogEntry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_log (
    call_id                TEXT PRIMARY KEY,
    session_id             TEXT NOT NULL,
    product_id             TEXT NOT NULL,    -- multi-tenant key; "default" for now
    page                   TEXT NOT NULL,
    tool                   TEXT NOT NULL,
    source                 TEXT NOT NULL,
    ok                     INTEGER NOT NULL,
    passed                 INTEGER,          -- NULL when verify never ran
    failed                 INTEGER NOT NULL, -- denormalised: the REFLECTING filter
    timestamp              TEXT NOT NULL,    -- ISO-8601 UTC
    tool_call              TEXT NOT NULL,    -- JSON
    expected_postcondition TEXT NOT NULL,    -- JSON
    actual_result          TEXT NOT NULL,    -- JSON
    verify                 TEXT              -- JSON, NULL when verify never ran
);
CREATE INDEX IF NOT EXISTS action_log_session ON action_log (session_id, timestamp);
CREATE INDEX IF NOT EXISTS action_log_failures ON action_log (session_id, failed);
CREATE INDEX IF NOT EXISTS action_log_product ON action_log (product_id, timestamp);

CREATE TABLE IF NOT EXISTS demo_runs (
    session_id     TEXT PRIMARY KEY,
    demo_id        TEXT NOT NULL,
    product_id     TEXT NOT NULL,
    platform       TEXT NOT NULL,
    status         TEXT NOT NULL,
    origin         TEXT NOT NULL DEFAULT 'dashboard_test',
    host_os        TEXT NOT NULL DEFAULT '',
    host_release   TEXT NOT NULL DEFAULT '',
    host_machine   TEXT NOT NULL DEFAULT '',
    host_name      TEXT NOT NULL DEFAULT '',
    browser        TEXT NOT NULL DEFAULT '',
    meeting_label  TEXT NOT NULL DEFAULT '',
    started_at     TEXT NOT NULL,
    ended_at       TEXT
);
CREATE INDEX IF NOT EXISTS demo_runs_product_started
    ON demo_runs (product_id, started_at);

CREATE TABLE IF NOT EXISTS llm_token_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      TEXT NOT NULL,
    session_id      TEXT,
    provider        TEXT NOT NULL,
    purpose         TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    billed_to       TEXT NOT NULL,
    timestamp       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS llm_token_usage_product
    ON llm_token_usage (product_id, timestamp);
"""


def utcnow() -> datetime:
    """Timestamp source for log entries. One place so tests can freeze it."""
    return datetime.now(timezone.utc)


class ActionLog:
    """Append-only log of tool calls, keyed by call and queryable by session.

    One connection per thread: a demo runs on its own thread while API handlers
    read the log from a threadpool, and sqlite3 forbids sharing a connection
    across threads. WAL mode so those readers don't block the writing demo.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if self.db_path.parent != Path():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add demo_runs.origin to a DB created before the column existed.

        Pre-existing rows predate the test/live split, so they cannot be proven
        billable -- they backfill to dashboard_test rather than inflate usage.
        """
        cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(demo_runs)").fetchall()
        }
        if "origin" not in cols:
            self._conn.execute(
                "ALTER TABLE demo_runs ADD COLUMN origin TEXT NOT NULL "
                "DEFAULT 'dashboard_test'"
            )

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

    def append(self, entry: ActionLogEntry) -> None:
        self._conn.execute(
            """
            INSERT INTO action_log (
                call_id, session_id, product_id, page, tool, source, ok, passed,
                failed, timestamp, tool_call, expected_postcondition, actual_result,
                verify
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(entry.call_id),
                str(entry.session_id),
                entry.product_id,
                entry.page,
                entry.tool_call.tool,
                entry.source,
                int(entry.actual_result.ok),
                None if entry.verify is None else int(entry.verify.passed),
                int(entry.failed),
                entry.timestamp.isoformat(),
                entry.tool_call.model_dump_json(),
                entry.expected_postcondition.model_dump_json(),
                entry.actual_result.model_dump_json(),
                None if entry.verify is None else entry.verify.model_dump_json(),
            ),
        )

    # -- read ----------------------------------------------------------------

    def entries(
        self, session_id: UUID, product_id: str | None = None
    ) -> list[ActionLogEntry]:
        """Every entry for a session, oldest first.

        `product_id` is defence in depth: session ids are UUIDs and already
        unguessable, but a multi-tenant read path should not depend on that.
        """
        return self._query(
            "SELECT * FROM action_log WHERE session_id = ?"
            + _and_product(product_id)
            + " ORDER BY timestamp, rowid",
            _params(session_id, product_id),
        )

    def failures(
        self, session_id: UUID, product_id: str | None = None
    ) -> list[ActionLogEntry]:
        """Entries whose action or postcondition failed -- the REFLECTING input."""
        return self._query(
            "SELECT * FROM action_log WHERE session_id = ? AND failed = 1"
            + _and_product(product_id)
            + " ORDER BY timestamp, rowid",
            _params(session_id, product_id),
        )

    def product_failures(self, product_id: str, limit: int = 500) -> list[ActionLogEntry]:
        """Failures across every demo of one product.

        What post-call reflection batches over, and what shows a customer which of
        their flows is actually rotting.
        """
        return self._query(
            "SELECT * FROM action_log WHERE product_id = ? AND failed = 1 "
            "ORDER BY timestamp DESC, rowid DESC LIMIT ?",
            (product_id, limit),
        )

    def product_metrics(
        self, product_id: str, days: int = 14, *, now: datetime | None = None
    ) -> dict:
        """Rolled-up counters + a daily series for one product's dashboard.

        Counts LIVE demos only. A Client running test demos from their dashboard
        must not move their own usage numbers -- see docs/PRODUCT_MODEL.md. Test
        runs are reported separately under `test_sessions` so the dashboard can
        still show them, clearly labelled as non-billable.

        Aggregated in SQL rather than by hydrating entries: the action log is the
        highest-volume table here and a dashboard poll must not scan it row by row.
        """
        when = now or utcnow()
        cutoff = (when - timedelta(days=max(1, days))).isoformat()
        # action_log has no origin of its own; a session's origin lives on its
        # demo_runs row. Subtracting test sessions (rather than selecting live
        # ones) means a run row that failed to persist still bills, instead of
        # a bookkeeping failure silently erasing a Client's usage.
        live_only = (
            "AND session_id NOT IN (SELECT session_id FROM demo_runs "
            "WHERE product_id = ? AND origin = 'dashboard_test') "
            "AND timestamp >= ? "
        )
        totals = self._conn.execute(
            "SELECT COUNT(*) AS actions, "
            "COUNT(DISTINCT session_id) AS sessions, "
            "COALESCE(SUM(failed), 0) AS failures, "
            "COALESCE(SUM(passed IS NOT NULL), 0) AS verified, "
            "COALESCE(SUM(passed = 1), 0) AS passed, "
            "MAX(timestamp) AS last_seen "
            "FROM action_log WHERE product_id = ? " + live_only,
            (product_id, product_id, cutoff),
        ).fetchone()

        rows = self._conn.execute(
            "SELECT substr(timestamp, 1, 10) AS day, "
            "COUNT(*) AS actions, "
            "COUNT(DISTINCT session_id) AS sessions, "
            "COALESCE(SUM(failed), 0) AS failures "
            "FROM action_log WHERE product_id = ? " + live_only +
            "GROUP BY day ORDER BY day DESC LIMIT ?",
            (product_id, product_id, cutoff, max(1, days)),
        ).fetchall()

        test_sessions = self._conn.execute(
            "SELECT COUNT(*) AS n FROM demo_runs "
            "WHERE product_id = ? AND origin = 'dashboard_test' AND started_at >= ?",
            (product_id, cutoff),
        ).fetchone()

        return {
            "test_sessions": test_sessions["n"] or 0,
            "actions": totals["actions"] or 0,
            "sessions": totals["sessions"] or 0,
            "failures": totals["failures"] or 0,
            "verified": totals["verified"] or 0,
            "passed": totals["passed"] or 0,
            "last_seen": totals["last_seen"],
            "series": [
                {
                    "day": r["day"],
                    "actions": r["actions"],
                    "sessions": r["sessions"],
                    "failures": r["failures"],
                }
                for r in reversed(rows)
            ],
        }

    def record_llm_usage(
        self,
        *,
        product_id: str,
        provider: str,
        purpose: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        billed_to: str,
        session_id: str | None = None,
        when: datetime | None = None,
    ) -> None:
        ts = (when or utcnow()).isoformat()
        self._conn.execute(
            "INSERT INTO llm_token_usage "
            "(product_id, session_id, provider, purpose, model, "
            "input_tokens, output_tokens, billed_to, timestamp) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                product_id,
                session_id,
                provider,
                purpose,
                model,
                max(0, int(input_tokens)),
                max(0, int(output_tokens)),
                billed_to,
                ts,
            ),
        )
        self._conn.commit()

    def llm_token_metrics(
        self, product_id: str, days: int = 14, *, now: datetime | None = None
    ) -> dict:
        """Roll up LLM tokens by provider and billing source for the dashboard."""
        when = now or utcnow()
        cutoff = (when - timedelta(days=max(1, days))).isoformat()
        rows = self._conn.execute(
            "SELECT provider, billed_to, "
            "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
            "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
            "COUNT(*) AS calls "
            "FROM llm_token_usage "
            "WHERE product_id = ? AND timestamp >= ? "
            "GROUP BY provider, billed_to "
            "ORDER BY provider, billed_to",
            (product_id, cutoff),
        ).fetchall()
        providers: list[dict] = []
        total_in = 0
        total_out = 0
        total_calls = 0
        for r in rows:
            inp = int(r["input_tokens"] or 0)
            out = int(r["output_tokens"] or 0)
            calls = int(r["calls"] or 0)
            total_in += inp
            total_out += out
            total_calls += calls
            providers.append(
                {
                    "provider": r["provider"],
                    "billed_to": r["billed_to"],
                    "input_tokens": inp,
                    "output_tokens": out,
                    "total_tokens": inp + out,
                    "calls": calls,
                }
            )
        return {
            "days": days,
            "providers": providers,
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "calls": total_calls,
        }

    def llm_token_metrics_by_model(
        self,
        product_id: str,
        days: int = 14,
        *,
        billed_to: str = "client",
        now: datetime | None = None,
    ) -> list[dict]:
        """Per-model rollups for Client BYOK usage on the dashboard."""
        when = now or utcnow()
        cutoff = (when - timedelta(days=max(1, days))).isoformat()
        rows = self._conn.execute(
            "SELECT model, "
            "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
            "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
            "COUNT(*) AS calls "
            "FROM llm_token_usage "
            "WHERE product_id = ? AND timestamp >= ? AND billed_to = ? "
            "AND TRIM(model) != '' "
            "GROUP BY model "
            "ORDER BY (input_tokens + output_tokens) DESC, model",
            (product_id, cutoff, billed_to),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            inp = int(r["input_tokens"] or 0)
            out.append(
                {
                    "model": r["model"],
                    "input_tokens": inp,
                    "output_tokens": int(r["output_tokens"] or 0),
                    "total_tokens": inp + int(r["output_tokens"] or 0),
                    "calls": int(r["calls"] or 0),
                }
            )
        return out

    def demo_run_metrics(
        self, product_id: str, days: int = 14, *, now: datetime | None = None
    ) -> dict:
        """Durable demo_runs rollups for the Overview Sessions card.

        Unlike ``product_metrics`` (billable action_log only), this counts every
        persisted demo run — test and live — so the dashboard reflects what the
        Client actually ran.
        """
        when = now or utcnow()
        cutoff = (when - timedelta(days=max(1, days))).isoformat()

        def _totals(origin: str | None) -> dict[str, int]:
            where = "product_id = ? AND started_at >= ?"
            params: list[str] = [product_id, cutoff]
            if origin is not None:
                where += " AND origin = ?"
                params.append(origin)
            row = self._conn.execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(status = 'failed'), 0) AS failed, "
                "COALESCE(SUM(status IN ('starting', 'running')), 0) AS running "
                f"FROM demo_runs WHERE {where}",
                params,
            ).fetchone()
            return {
                "total": int(row["total"] or 0),
                "failed": int(row["failed"] or 0),
                "running": int(row["running"] or 0),
            }

        rows = self._conn.execute(
            "SELECT substr(started_at, 1, 10) AS day, COUNT(*) AS sessions "
            "FROM demo_runs WHERE product_id = ? AND started_at >= ? "
            "GROUP BY day ORDER BY day DESC LIMIT ?",
            (product_id, cutoff, max(1, days)),
        ).fetchall()
        all_totals = _totals(None)
        return {
            "series": [
                {
                    "day": r["day"],
                    "sessions": int(r["sessions"]),
                    "actions": 0,
                    "failures": 0,
                }
                for r in reversed(rows)
            ],
            "total": all_totals["total"],
            "running": all_totals["running"],
            "failed": all_totals["failed"],
            "live": _totals("public_embed"),
            "test": _totals("dashboard_test"),
        }

    def dashboard_metrics(
        self, product_id: str, days: int = 14, *, now: datetime | None = None
    ) -> dict:
        """Unified Client dashboard KPIs — one ``days`` window, test + live included.

        ``sessions`` / daily ``series[].sessions`` = demo run count (``demo_runs``).
        ``failures`` / ``series[].failures`` = failed tool steps (``action_log``).
        ``failed_runs`` = demos whose run ``status`` is ``failed``.
        """
        when = now or utcnow()
        cutoff = (when - timedelta(days=max(1, days))).isoformat()
        runs = self.demo_run_metrics(product_id, days=days, now=when)

        totals = self._conn.execute(
            """
            SELECT COUNT(*) AS actions,
                   COALESCE(SUM(al.failed), 0) AS failures,
                   COALESCE(SUM(al.passed IS NOT NULL), 0) AS verified,
                   COALESCE(SUM(al.passed = 1), 0) AS passed,
                   MAX(al.timestamp) AS last_seen
            FROM action_log al
            INNER JOIN demo_runs dr
              ON dr.session_id = al.session_id AND dr.product_id = al.product_id
            WHERE dr.product_id = ? AND dr.started_at >= ?
            """,
            (product_id, cutoff),
        ).fetchone()

        action_rows = self._conn.execute(
            """
            SELECT substr(al.timestamp, 1, 10) AS day,
                   COUNT(*) AS actions,
                   COALESCE(SUM(al.failed), 0) AS failures
            FROM action_log al
            INNER JOIN demo_runs dr
              ON dr.session_id = al.session_id AND dr.product_id = al.product_id
            WHERE dr.product_id = ? AND dr.started_at >= ?
            GROUP BY day
            """,
            (product_id, cutoff),
        ).fetchall()

        run_days = {r["day"]: int(r["sessions"]) for r in runs["series"]}
        action_days = {
            r["day"]: {"actions": int(r["actions"]), "failures": int(r["failures"])}
            for r in action_rows
        }
        all_days = sorted(set(run_days) | set(action_days))

        series = [
            {
                "day": day,
                "sessions": run_days.get(day, 0),
                "actions": action_days.get(day, {}).get("actions", 0),
                "failures": action_days.get(day, {}).get("failures", 0),
            }
            for day in all_days
        ]

        visitor = self.product_metrics(product_id, days=days, now=when)

        runs_with_step_failures = self._conn.execute(
            """
            SELECT COUNT(*) AS n FROM demo_runs dr
            WHERE dr.product_id = ? AND dr.started_at >= ?
              AND EXISTS (
                SELECT 1 FROM action_log al
                WHERE al.session_id = dr.session_id
                  AND al.product_id = dr.product_id
                  AND al.failed = 1
              )
            """,
            (product_id, cutoff),
        ).fetchone()

        return {
            "days": days,
            "test_sessions": runs["test"]["total"],
            "sessions": runs["total"],
            "actions": int(totals["actions"] or 0),
            "failures": int(totals["failures"] or 0),
            "failed_runs": runs["failed"],
            "runs_with_step_failures": int(runs_with_step_failures["n"] or 0),
            "verified": int(totals["verified"] or 0),
            "passed": int(totals["passed"] or 0),
            "last_seen": totals["last_seen"],
            "series": series,
            "demos": {
                "total": runs["total"],
                "running": runs["running"],
                "failed": runs["failed"],
            },
            "live": runs["live"],
            "test": runs["test"],
            "visitor": {
                "sessions": visitor["sessions"],
                "actions": visitor["actions"],
                "failures": visitor["failures"],
            },
        }

    def sessions(self) -> list[UUID]:
        rows = self._conn.execute(
            "SELECT session_id, MIN(timestamp) AS started FROM action_log "
            "GROUP BY session_id ORDER BY started"
        ).fetchall()
        return [UUID(r["session_id"]) for r in rows]

    # -- demo runs -----------------------------------------------------------

    def upsert_run(
        self,
        *,
        session_id: UUID,
        demo_id: UUID,
        product_id: str,
        platform: str,
        status: str,
        origin: str,
        host_os: str = "",
        host_release: str = "",
        host_machine: str = "",
        host_name: str = "",
        browser: str = "",
        meeting_label: str = "",
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        started = (started_at or utcnow()).isoformat()
        ended = None if ended_at is None else ended_at.isoformat()
        self._conn.execute(
            """
            INSERT INTO demo_runs (
                session_id, demo_id, product_id, platform, status, origin,
                host_os, host_release, host_machine, host_name, browser,
                meeting_label, started_at, ended_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                demo_id=excluded.demo_id,
                product_id=excluded.product_id,
                platform=excluded.platform,
                status=excluded.status,
                host_os=excluded.host_os,
                host_release=excluded.host_release,
                host_machine=excluded.host_machine,
                host_name=excluded.host_name,
                browser=excluded.browser,
                meeting_label=excluded.meeting_label,
                started_at=excluded.started_at,
                ended_at=excluded.ended_at
            """,
            # origin is absent from DO UPDATE on purpose: a later status upsert
            # must not be able to reclassify a billable live run as a test.
            (
                str(session_id),
                str(demo_id),
                product_id,
                platform,
                status,
                origin,
                host_os,
                host_release,
                host_machine,
                host_name,
                browser,
                meeting_label,
                started,
                ended,
            ),
        )

    def update_run_status(
        self,
        session_id: UUID,
        status: str,
        ended_at: datetime | None = None,
    ) -> None:
        ended = None if ended_at is None else ended_at.isoformat()
        self._conn.execute(
            "UPDATE demo_runs SET status = ?, ended_at = COALESCE(?, ended_at) "
            "WHERE session_id = ?",
            (status, ended, str(session_id)),
        )

    def get_run(self, session_id: UUID, product_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM demo_runs WHERE session_id = ? AND product_id = ?",
            (str(session_id), product_id),
        ).fetchone()
        return None if row is None else _row_to_run(row, self._fail_count(row))

    def get_run_by_demo_id(self, demo_id: UUID, product_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM demo_runs WHERE demo_id = ? AND product_id = ?",
            (str(demo_id), product_id),
        ).fetchone()
        return None if row is None else _row_to_run(row, self._fail_count(row))

    def list_runs(
        self,
        product_id: str,
        days: int = 7,
        now: datetime | None = None,
    ) -> list[dict]:
        self.prune_runs(days=days, now=now)
        when = now or utcnow()
        cutoff = (when - timedelta(days=max(1, days))).isoformat()
        rows = self._conn.execute(
            "SELECT * FROM demo_runs WHERE product_id = ? AND started_at >= ? "
            "ORDER BY started_at DESC, session_id DESC",
            (product_id, cutoff),
        ).fetchall()
        return [_row_to_run(r, self._fail_count(r)) for r in rows]

    def prune_runs(self, days: int = 7, now: datetime | None = None) -> None:
        when = now or utcnow()
        cutoff = (when - timedelta(days=max(1, days))).isoformat()
        old = self._conn.execute(
            "SELECT session_id FROM demo_runs WHERE started_at < ?",
            (cutoff,),
        ).fetchall()
        sids = [r["session_id"] for r in old]
        self._conn.execute("DELETE FROM demo_runs WHERE started_at < ?", (cutoff,))
        for sid in sids:
            self._conn.execute("DELETE FROM action_log WHERE session_id = ?", (sid,))

    def _fail_count(self, row: sqlite3.Row) -> int:
        r = self._conn.execute(
            "SELECT COALESCE(SUM(failed), 0) AS n FROM action_log "
            "WHERE session_id = ? AND product_id = ?",
            (row["session_id"], row["product_id"]),
        ).fetchone()
        return int(r["n"] or 0)

    def _query(self, sql: str, params: tuple) -> list[ActionLogEntry]:
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close this thread's connection. Other threads' connections are theirs
        to close; WAL leaves the file consistent either way."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def __enter__(self) -> ActionLog:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _and_product(product_id: str | None) -> str:
    return "" if product_id is None else " AND product_id = ?"


def _params(session_id: UUID, product_id: str | None) -> tuple:
    return (str(session_id),) if product_id is None else (str(session_id), product_id)


def _row_to_run(row: sqlite3.Row, fail_count: int) -> dict:
    return {
        "session_id": row["session_id"],
        "demo_id": row["demo_id"],
        "product_id": row["product_id"],
        "platform": row["platform"],
        "status": row["status"],
        "origin": row["origin"],
        "host_os": row["host_os"],
        "host_release": row["host_release"],
        "host_machine": row["host_machine"],
        "host_name": row["host_name"],
        "browser": row["browser"],
        "meeting_label": row["meeting_label"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "fail_count": fail_count,
    }


def _row_to_entry(row: sqlite3.Row) -> ActionLogEntry:
    return ActionLogEntry.model_validate(
        {
            "call_id": row["call_id"],
            "session_id": row["session_id"],
            "product_id": row["product_id"],
            "page": row["page"],
            "source": row["source"],
            "timestamp": row["timestamp"],
            "tool_call": json.loads(row["tool_call"]),
            "expected_postcondition": json.loads(row["expected_postcondition"]),
            "actual_result": json.loads(row["actual_result"]),
            "verify": None if row["verify"] is None else json.loads(row["verify"]),
        }
    )
