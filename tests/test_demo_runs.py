"""demo_runs: persisted run meta + 7-day prune, product-scoped."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from navigator.logs.store import ActionLog

TS = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _run(
    log: ActionLog,
    *,
    session_id,
    product_id: str,
    started_at: datetime,
    platform: str = "static",
    status: str = "finished",
) -> None:
    log.upsert_run(
        session_id=session_id,
        demo_id=uuid4(),
        product_id=product_id,
        platform=platform,
        status=status,
        host_os="Linux",
        host_release="7.0",
        host_machine="x86_64",
        host_name="devbox",
        browser="",
        meeting_label="meet:haw-cyyt-ynv",
        started_at=started_at,
    )


def test_upsert_and_list_runs_scoped_by_product(tmp_path):
    with ActionLog(tmp_path / "t.db") as log:
        a, b = uuid4(), uuid4()
        _run(log, session_id=a, product_id="acme", started_at=TS, status="running")
        _run(
            log,
            session_id=b,
            product_id="globex",
            started_at=TS,
            platform="zoom",
            status="finished",
        )
        rows = log.list_runs("acme", days=7, now=TS)
        assert len(rows) == 1
        assert rows[0]["session_id"] == str(a)
        assert rows[0]["platform"] == "static"


def test_prune_drops_runs_older_than_days(tmp_path):
    with ActionLog(tmp_path / "t.db") as log:
        old_sid, new_sid = uuid4(), uuid4()
        _run(
            log,
            session_id=old_sid,
            product_id="acme",
            started_at=TS - timedelta(days=8),
            platform="meet",
        )
        _run(
            log,
            session_id=new_sid,
            product_id="acme",
            started_at=TS,
            platform="meet",
        )
        log.prune_runs(days=7, now=TS)
        rows = log.list_runs("acme", days=7, now=TS)
        assert [r["session_id"] for r in rows] == [str(new_sid)]


def test_get_run_by_demo_id(tmp_path):
    from datetime import datetime, timezone
    from uuid import uuid4

    from navigator.logs.store import ActionLog

    ts = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    with ActionLog(tmp_path / "t.db") as log:
        sid, did = uuid4(), uuid4()
        log.upsert_run(
            session_id=sid,
            demo_id=did,
            product_id="acme",
            platform="static",
            status="running",
            host_os="Linux",
            host_release="1",
            host_machine="x86_64",
            host_name="h",
            browser="",
            meeting_label="meet",
            started_at=ts,
        )
        assert log.get_run_by_demo_id(did, "acme")["session_id"] == str(sid)
        assert log.get_run_by_demo_id(did, "other") is None
