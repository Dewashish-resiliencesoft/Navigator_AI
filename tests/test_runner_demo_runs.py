"""DemoRunner upserts demo_runs on start_live."""

from __future__ import annotations

from pathlib import Path

from navigator.app.runner import DemoRunner
from navigator.knowledge.site_graph import load_site_graph
from navigator.logs.store import ActionLog

SEED = Path(__file__).parent.parent / "navigator/knowledge/sites/whatsapp_crm.yaml"


def test_start_live_persists_demo_run(tmp_path):
    graph = load_site_graph(SEED)
    page_id = next(iter(graph.pages))
    flow_id = next(iter(graph.page(page_id).flows))
    db = tmp_path / "r.db"
    runner = DemoRunner(str(db), redis_url=None)

    def fake_run(**kwargs):
        return "bot-fake"

    handle = runner.start_live(
        "acme",
        graph,
        1,
        (page_id, flow_id),
        meeting_url="https://meet.google.com/haw-cyyt-ynv?pwd=SECRET",
        platform="static",
        run=fake_run,
    )
    runner.wait(handle.demo_id, timeout=10)

    with ActionLog(db) as log:
        rows = log.list_runs("acme", days=7)
    assert len(rows) == 1
    assert rows[0]["session_id"] == str(handle.session_id)
    assert rows[0]["status"] == "finished"
    assert "secret" not in rows[0]["meeting_label"].lower()
    assert rows[0]["meeting_label"].startswith("meet:")
