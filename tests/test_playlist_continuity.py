"""Playlist advance should not reload when the next selector is already visible."""

from __future__ import annotations

from unittest.mock import MagicMock

from navigator.agent.nodes.planning import _can_continue_in_place
from navigator.agent.state import CallDeps
from navigator.core.schemas import ClickElement, Postcondition
from navigator.knowledge.site_graph import load_site_graph
from pathlib import Path


FIXTURE = Path(__file__).parent.parent / "navigator/knowledge/sites/whatsapp_crm.yaml"


def test_can_continue_in_place_when_selector_visible(page, log, tmp_path):
    graph = load_site_graph(FIXTURE)
    page.set_content('<button id="missing">Compose</button>')
    deps = CallDeps(
        graph=graph,
        page=page,
        log=log,
        speaker=MagicMock(),
        archive_dir=tmp_path / "archives",
    )
    first = ClickElement(
        selector="composer",
        expects=Postcondition(check="visible", selector="composer"),
    )
    assert _can_continue_in_place(deps, "inbox", first) is False

    page.set_content('<button id="composer">Compose</button>')
    assert _can_continue_in_place(deps, "inbox", first) is True
