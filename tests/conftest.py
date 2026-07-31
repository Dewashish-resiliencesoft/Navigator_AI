from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from navigator.agent.state import CallDeps, initial_state
from navigator.config.site_graph import load_site_graph
from navigator.logs.store import ActionLog
from navigator.voice.tts import PrintSpeaker

FIXTURES = Path(__file__).parent / "fixtures"
SEED_GRAPH = Path(__file__).parent.parent / "navigator/config/sites/whatsapp_crm.yaml"


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
