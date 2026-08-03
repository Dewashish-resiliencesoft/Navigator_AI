"""Tier 2 proposer: inventory → utterance-aware choice → ClickElement."""

from __future__ import annotations

from navigator.agent.tier2_propose import (
    bind_ephemeral_selector,
    choose_for_utterance,
    clickable_inventory,
    propose_from_page,
)
from navigator.core.schemas import ClickElement
from navigator.knowledge.site_graph import parse_site_graph


def test_choose_for_utterance_picks_index():
    elements = [
        {"tag": "button", "text": "Settings", "fillable": False, "label": "Settings"},
        {"tag": "button", "text": "Delete all", "fillable": False, "label": "Delete all"},
    ]

    def ask(_prompt: str) -> str:
        return '{"index": 0, "why": "settings is safe", "narration": "Opening Settings."}'

    choice = choose_for_utterance(
        utterance="where are settings?",
        url="https://example.com",
        elements=elements,
        ask_text=ask,
    )
    assert choice is not None
    assert choice.index == 0
    assert "Settings" in choice.narration


def test_clickable_inventory_drops_fillable(monkeypatch):
    raw = [
        {"tag": "input", "text": "", "fillable": True, "type": "text", "id": "name"},
        {
            "tag": "button",
            "text": "Reports",
            "fillable": False,
            "id": "reports-btn",
            "testid": "",
            "name": "",
        },
    ]
    monkeypatch.setattr(
        "navigator.agent.tier2_propose.perceive.inventory", lambda _page: raw
    )
    out = clickable_inventory(object())
    assert len(out) == 1
    assert out[0]["text"] == "Reports"


def test_propose_from_page_returns_click(monkeypatch):
    raw = [
        {
            "tag": "a",
            "text": "Billing overview",
            "fillable": False,
            "id": "billing",
            "testid": "",
            "name": "",
            "href": "/billing",
        }
    ]
    monkeypatch.setattr(
        "navigator.agent.tier2_propose.perceive.inventory", lambda _page: raw
    )
    monkeypatch.setattr(
        "navigator.agent.tier2_propose.perceive.screenshot_b64", lambda _page: ""
    )

    class FakePage:
        url = "https://example.com/app"

    prop = propose_from_page(
        utterance="how does billing work?",
        page=FakePage(),
        ask_text=lambda _p: (
            '{"index": 0, "why": "billing link", "narration": "Opening billing."}'
        ),
    )
    assert prop is not None
    assert isinstance(prop.call, ClickElement)
    assert prop.element["_tier2_alias"]
    assert prop.element["_tier2_css"]
    assert "billing" in prop.spoken.lower() or "Opening" in prop.spoken


def test_bind_ephemeral_selector_adds_alias():
    graph = parse_site_graph(
        """
version: 1
site: demo
base_url: https://example.com/
pages:
  main:
    name: Main
    url: /
    selectors:
      body: body
    flows: {}
"""
    )
    updated = bind_ephemeral_selector(graph, "main", "billing", "#billing")
    assert updated.selector("main", "billing") == "#billing"
    assert graph.page("main").selectors.get("billing") is None  # original frozen
