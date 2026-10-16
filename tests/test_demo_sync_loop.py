"""The demo-state sync loop must exit when the app shuts down."""

from __future__ import annotations

import time

from navigator.app.runner import DemoRunner


def _drive(async_fn) -> None:
    """Run one coroutine on a private loop.

    The full suite sometimes already has a loop on this thread (Playwright).
    ``asyncio.run`` refuses to start in that case, so the lifespan is driven
    on a worker thread instead.
    """
    import asyncio
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(async_fn())).result(timeout=5)


def test_sync_loop_is_a_daemon_and_stops_promptly():
    runner = DemoRunner(":memory:", sync_enabled=True)
    thread = runner._sync_thread
    assert thread is not None
    assert thread.daemon is True
    assert thread.is_alive()

    started = time.monotonic()
    runner.stop_sync()
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert not thread.is_alive()


def test_sync_loop_stays_off_when_setting_disabled(monkeypatch):
    from navigator.core.settings import settings

    monkeypatch.setattr(settings, "demo_sync_enabled", False)
    runner = DemoRunner(":memory:")
    assert runner._sync_thread is None


def test_lifespan_exit_stops_the_sync_loop(monkeypatch):
    import navigator.app.deps as deps

    runner = DemoRunner(":memory:", sync_enabled=True)
    monkeypatch.setattr(deps, "_runner", runner)
    monkeypatch.setattr(
        "navigator.automation.playwright_env.ensure_playwright_browsers",
        lambda: None,
    )
    monkeypatch.setattr(
        "navigator.meeting.attendee_stack.ensure_attendee_stack",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr("navigator.storage.env_load.load_cloud_env", lambda: None)
    monkeypatch.setattr("navigator.storage.mongo.mongo_enabled", lambda: False)
    monkeypatch.setattr("navigator.storage.media.cloudinary_enabled", lambda: False)
    monkeypatch.setattr("navigator.meeting.warm_pool.start_warm_pool", lambda: None)
    monkeypatch.setattr(
        "navigator.meeting.zoom_host.ensure_public_base_url",
        lambda: "http://127.0.0.1",
    )

    class _Pool:
        def stop(self) -> None:
            return None

    monkeypatch.setattr("navigator.meeting.warm_pool.get_warm_pool", lambda: _Pool())

    async def _run() -> None:
        async with deps._lifespan(None):
            assert runner._sync_thread is not None
            assert runner._sync_thread.is_alive()

    started = time.monotonic()
    _drive(_run)
    assert time.monotonic() - started < 2.0
    assert runner._sync_stop.is_set()
    assert not runner._sync_thread.is_alive()


def test_lifespan_crash_still_stops_the_sync_loop(monkeypatch):
    import pytest

    import navigator.app.deps as deps

    runner = DemoRunner(":memory:", sync_enabled=True)
    monkeypatch.setattr(deps, "_runner", runner)
    monkeypatch.setattr(
        "navigator.automation.playwright_env.ensure_playwright_browsers",
        lambda: None,
    )
    monkeypatch.setattr(
        "navigator.meeting.attendee_stack.ensure_attendee_stack",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr("navigator.storage.env_load.load_cloud_env", lambda: None)
    monkeypatch.setattr("navigator.storage.mongo.mongo_enabled", lambda: False)
    monkeypatch.setattr("navigator.storage.media.cloudinary_enabled", lambda: False)
    monkeypatch.setattr("navigator.meeting.warm_pool.start_warm_pool", lambda: None)
    monkeypatch.setattr(
        "navigator.meeting.zoom_host.ensure_public_base_url",
        lambda: "http://127.0.0.1",
    )

    class _Pool:
        def stop(self) -> None:
            return None

    monkeypatch.setattr("navigator.meeting.warm_pool.get_warm_pool", lambda: _Pool())

    async def _run() -> None:
        with pytest.raises(RuntimeError, match="boom"):
            async with deps._lifespan(None):
                raise RuntimeError("boom")

    _drive(_run)
    assert runner._sync_stop.is_set()
    assert runner._sync_thread is not None
    assert not runner._sync_thread.is_alive()


def test_expected_path_keeps_leading_slash_when_base_url_empty():
    from navigator.agent.nodes.executing import expected_path
    from navigator.core.schemas import Navigate, Postcondition
    from navigator.knowledge.site_graph import PageSpec, SiteGraph

    graph = SiteGraph(
        version=1,
        site="acme",
        base_url="",
        pages={"inbox": PageSpec(name="Inbox", url="inbox", selectors={})},
    )
    path = expected_path(
        graph,
        Navigate(page_id="inbox", expects=Postcondition(check="visible", selector="main")),
    )
    assert path == "/inbox"
