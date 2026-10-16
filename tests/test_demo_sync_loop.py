"""The demo-state sync loop must exit when the app shuts down."""

from __future__ import annotations

import time

from navigator.app.runner import DemoRunner


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
    import asyncio

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
    asyncio.run(_run())
    assert time.monotonic() - started < 2.0
    assert runner._sync_stop.is_set()
    assert not runner._sync_thread.is_alive()
