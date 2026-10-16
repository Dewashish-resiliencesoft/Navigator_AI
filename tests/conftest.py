from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

# Before Navigator settings load: DemoRunner starts the sync loop at import.
os.environ["NAVIGATOR_DEMO_SYNC_ENABLED"] = "0"

import pytest

from navigator.agent.state import CallDeps, initial_state
from navigator.knowledge.site_graph import load_site_graph
from navigator.logs.store import ActionLog
from navigator.voice.tts import PrintSpeaker

FIXTURES = Path(__file__).parent / "fixtures"
SEED_GRAPH = Path(__file__).parent.parent / "navigator/knowledge/sites/whatsapp_crm.yaml"


@pytest.fixture(autouse=True)
def _disable_demo_sync(monkeypatch):
    """Keep the demo-state sync loop from outliving the FastAPI test client."""
    monkeypatch.setenv("NAVIGATOR_DEMO_SYNC_ENABLED", "0")
    from navigator.core.settings import settings

    monkeypatch.setattr(settings, "demo_sync_enabled", False)


@pytest.fixture(autouse=True)
def _fast_cursor_motion(monkeypatch):
    """Collapse demo cursor waits/animations in unit tests."""
    monkeypatch.setattr(
        "navigator.automation.browser.cursor.MOTION_SCALE",
        0.0,
    )


@pytest.fixture
def voice_keys_ready(monkeypatch):
    """Satisfy demo-start readiness without changing the production checks.

    Live voice is ready when a Gemini key is set, Groq is required for
    conversational interrupts, and a public demo also needs Attendee. These
    tests do not call those services. The production readiness checks stay in place.
    """
    from navigator.core.settings import settings

    monkeypatch.setattr(settings, "gemini_live_api_key", "", raising=False)
    monkeypatch.setattr(settings, "gemini_live_api_keys", "", raising=False)
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini-key", raising=False)
    monkeypatch.setattr(settings, "groq_api_key", "test-groq-key", raising=False)
    monkeypatch.setattr(
        "navigator.meeting.attendee_stack.attendee_reachable",
        lambda _url: True,
    )


def _run_coroutine(coro):
    """Run ``coro`` on a private loop.

    Playwright's sync driver already has a loop marked running on this thread,
    so ``asyncio.run`` raises. A worker thread has no such loop.
    """
    import asyncio
    import threading

    box: dict = {}

    def _go() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            box["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    worker = threading.Thread(target=_go)
    worker.start()
    worker.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """Await async tests without nesting ``asyncio.run`` on Playwright's loop."""
    import inspect

    func = pyfuncitem.obj
    if not inspect.iscoroutinefunction(func):
        return None
    kwargs = {
        name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames
    }
    _run_coroutine(func(**kwargs))
    return True


@pytest.fixture(autouse=True)
def _clear_semantic_cache():
    from navigator.agent.brain_router import get_semantic_cache

    get_semantic_cache()._data.clear()
    yield
    get_semantic_cache()._data.clear()


@pytest.fixture(scope="session")
def site_graph():
    return load_site_graph(SEED_GRAPH)


@pytest.fixture
def log(tmp_path):
    with ActionLog(tmp_path / "test.db") as store:
        yield store


@pytest.fixture(scope="session")
def browser():
    """One headless Chromium for the whole test session."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser, site_graph):
    """A page already on the inbox fixture."""
    context = browser.new_context()
    p = context.new_page()
    p.goto(site_graph.url_for("inbox"))
    yield p
    context.close()


@pytest.fixture
def deps(site_graph, page, log, tmp_path):
    return CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=("inbox", "send_test_message"),
        archive_dir=tmp_path / "archives",
    )


@pytest.fixture
def state():
    return initial_state(uuid4(), "inbox")
