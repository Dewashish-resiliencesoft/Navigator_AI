"""End must leave the Attendee bot — _stop alone is ignored by the live path."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from navigator.app.runner import DemoHandle, DemoRunner, _MAX_SAID


def _handle(**kw) -> DemoHandle:
    return DemoHandle(
        demo_id=uuid4(),
        product_id="acme",
        revision=1,
        session_id=uuid4(),
        origin="public_embed",
        status="running",
        **kw,
    )


def test_stop_leaves_attendee_bot():
    left: list[str] = []
    runner = DemoRunner(":memory:")
    handle = _handle(bot_id="bot-xyz")
    runner._demos[handle.demo_id] = handle

    out = runner.stop(handle.demo_id, "acme", leave_bot=left.append)

    assert out is handle
    assert handle._stop.is_set()
    assert left == ["bot-xyz"]


def test_stop_marks_finished_immediately_while_worker_alive():
    """End must not leave UI stuck on 'running' until the worker exits."""
    runner = DemoRunner(":memory:")
    handle = _handle()
    handle._thread = threading.Thread(target=lambda: time.sleep(30), daemon=True)
    handle._thread.start()
    runner._demos[handle.demo_id] = handle

    out = runner.stop(handle.demo_id, "acme", leave_bot=lambda _: None)

    assert out is handle
    assert handle._stop.is_set()
    assert handle.status == "finished"
    assert handle.finished_at is not None
    assert handle.bot_in_meeting is False
    assert any("demo ended" in s.lower() for s in handle.said)


def test_stop_without_bot_id_still_sets_event():
    runner = DemoRunner(":memory:")
    handle = _handle()
    runner._demos[handle.demo_id] = handle

    runner.stop(handle.demo_id, "acme", leave_bot=lambda _: None)

    assert handle._stop.is_set()


def test_live_worker_gets_stop_event_and_records_bot(site_graph):
    """_run_live must pass stop_event and wire on_bot_joined → handle.bot_id."""
    runner = DemoRunner(":memory:")
    seen: dict = {}

    def fake_run(**kwargs) -> str:
        seen.update(kwargs)
        on_joined = kwargs.get("on_bot_joined")
        if on_joined:
            on_joined("bot-from-run")
        return "bot-from-run"

    page_id = next(iter(site_graph.pages))
    flow_id = next(iter(site_graph.pages[page_id].flows))
    handle = runner.start_live(
        "acme",
        site_graph,
        revision=1,
        flow=(page_id, flow_id),
        meeting_url="https://meet.example/x",
        platform="google_meet",
        origin="public_embed",
        run=fake_run,
    )
    runner.wait(handle.demo_id, timeout=5.0)

    assert "stop_event" in seen
    assert isinstance(seen["stop_event"], threading.Event)
    assert seen["stop_event"] is handle._stop
    assert handle.bot_id == "bot-from-run"
    assert handle.status == "finished"


def test_live_worker_exposes_leave_grace_callback(site_graph):
    runner = DemoRunner(":memory:")
    ready = threading.Event()
    release = threading.Event()
    seen: dict = {}

    def fake_run(**kwargs) -> str:
        seen["on_leave_grace"] = kwargs["on_leave_grace"]
        ready.set()
        release.wait(timeout=5.0)
        return "bot-1"

    page_id = next(iter(site_graph.pages))
    flow_id = next(iter(site_graph.pages[page_id].flows))
    handle = runner.start_live(
        "acme",
        site_graph,
        revision=1,
        flow=(page_id, flow_id),
        meeting_url="https://meet.example/grace",
        platform="google_meet",
        origin="public_embed",
        run=fake_run,
    )
    assert ready.wait(timeout=5.0)
    seen["on_leave_grace"](25)
    assert handle.leave_grace_remaining == 25
    release.set()
    runner.wait(handle.demo_id, timeout=5.0)

    # The worker clears transient grace state when the run terminates.
    assert handle.leave_grace_remaining is None


def test_live_worker_sets_bot_in_meeting_when_ready(site_graph):
    runner = DemoRunner(":memory:")

    def fake_run(**kwargs) -> str:
        on_ready = kwargs.get("on_meeting_ready")
        on_joined = kwargs.get("on_bot_joined")
        if on_joined:
            on_joined("bot-1")
        if on_ready:
            on_ready(kwargs["meeting_url"])
        return "bot-1"

    page_id = next(iter(site_graph.pages))
    flow_id = next(iter(site_graph.pages[page_id].flows))
    handle = runner.start_live(
        "acme",
        site_graph,
        revision=1,
        flow=(page_id, flow_id),
        meeting_url="https://meet.example/ready",
        platform="google_meet",
        origin="public_embed",
        run=fake_run,
    )
    runner.wait(handle.demo_id, timeout=5.0)

    assert handle.bot_id == "bot-1"
    assert handle.bot_in_meeting is False
    assert any("join link ready" in s.lower() for s in handle.said)
    assert any("demo completed" in s.lower() for s in handle.said)


def test_live_worker_bot_in_meeting_false_until_ready(site_graph):
    runner = DemoRunner(":memory:")

    def fake_run(**kwargs) -> str:
        on_joined = kwargs.get("on_bot_joined")
        if on_joined:
            on_joined("bot-early")
        return "bot-early"

    page_id = next(iter(site_graph.pages))
    flow_id = next(iter(site_graph.pages[page_id].flows))
    handle = runner.start_live(
        "acme",
        site_graph,
        revision=1,
        flow=(page_id, flow_id),
        meeting_url="https://meet.example/early",
        platform="google_meet",
        origin="public_embed",
        run=fake_run,
    )
    runner.wait(handle.demo_id, timeout=5.0)

    assert handle.bot_id == "bot-early"
    assert handle.bot_in_meeting is False


def test_said_caps_at_max():
    handle = _handle()
    for i in range(_MAX_SAID + 20):
        handle.append_said(str(i))
    assert len(handle.said) == _MAX_SAID
    assert handle.said[0] == "20"
    assert handle.said[-1] == str(_MAX_SAID + 19)


def test_reap_drops_old_finished(tmp_path):
    runner = DemoRunner(str(tmp_path / "db.sqlite"))
    handle = _handle()
    handle.status = "finished"
    handle.finished_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    runner._demos[handle.demo_id] = handle
    runner._store.save(handle)

    assert runner._reap_finished(keep_s=1) == 1
    assert handle.demo_id not in runner._demos
    assert runner.get(handle.demo_id) is None


def test_reap_keeps_running(tmp_path):
    runner = DemoRunner(str(tmp_path / "db.sqlite"))
    handle = _handle()
    handle.status = "running"
    runner._demos[handle.demo_id] = handle
    assert runner._reap_finished(keep_s=0) == 0
    assert handle.demo_id in runner._demos


def test_live_wall_clock_sets_stop(site_graph, monkeypatch):
    monkeypatch.setattr("navigator.app.runner.LIVE_DEMO_WALL_S", 0.05)
    runner = DemoRunner(":memory:")
    seen = {}

    def fake_run(**kwargs) -> str:
        seen["stop"] = kwargs["stop_event"]
        assert kwargs["stop_event"].wait(timeout=2.0)
        return "bot-wall"

    page_id = next(iter(site_graph.pages))
    flow_id = next(iter(site_graph.pages[page_id].flows))
    handle = runner.start_live(
        "acme",
        site_graph,
        revision=1,
        flow=(page_id, flow_id),
        meeting_url="https://meet.example/wall",
        platform="google_meet",
        origin="public_embed",
        run=fake_run,
    )
    runner.wait(handle.demo_id, timeout=5.0)
    assert seen["stop"].is_set()
    assert handle.status == "finished"
