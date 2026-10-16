"""Viewport section awareness: on-screen name and nearby off-screen list."""

from __future__ import annotations

from navigator.agent.planner import build_prompt
from navigator.agent_runtime.dom.builder import build_dom_state
from navigator.automation.browser.screen_context import planner_screen, screen_snapshot
from navigator.automation.browser.viewport_awareness import (
    Placement,
    ViewportAwareness,
    bring_alias_into_view,
    classify_rect,
    measure_viewport,
    nearby_offscreen,
    pick_current,
)
from navigator.core.schemas import Persona
from navigator.knowledge.site_graph import PageSpec, SiteGraph


def _graph() -> SiteGraph:
    return SiteGraph(
        version=1,
        site="acme",
        base_url="https://example.test",
        pages={
            "inbox": PageSpec(
                name="Inbox",
                url="/app",
                selectors={"search_box": "#search", "composer": "#composer"},
            ),
            "billing": PageSpec(
                name="Billing",
                url="/billing",
                selectors={"invoice": "#invoice-secret"},
            ),
        },
    )


class _Page:
    url = "https://example.test/app"

    def __init__(self, raw: dict):
        self.raw = raw
        self.scrolls: list[dict] = []

    def title(self) -> str:
        return "App"

    def evaluate(self, _js, arg=None):
        if isinstance(arg, dict) and "delta" in arg:
            self.scrolls.append(arg)
            return True
        return self.raw


def _raw() -> dict:
    return {
        "vw": 800,
        "vh": 600,
        "sx": 0,
        "sy": 400,
        "measured": [
            {
                "id": "search_box",
                "missing": False,
                "top": 40,
                "bottom": 70,
                "left": 10,
                "right": 200,
            },
            {
                "id": "composer",
                "missing": False,
                "top": 700,
                "bottom": 760,
                "left": 10,
                "right": 200,
            },
        ],
        "headings": [
            {"text": "Inbox", "top": 20, "bottom": 50, "left": 0, "right": 400},
            {"text": "Header", "top": -200, "bottom": -160, "left": 0, "right": 400},
            {"text": "Billing", "top": 900, "bottom": 940, "left": 0, "right": 400},
        ],
    }


def test_classify_visible_partial_above_below():
    assert classify_rect(
        top=10, bottom=40, left=0, right=100, viewport_w=800, viewport_h=600
    )[0] == "visible"
    relation, distance, _ratio = classify_rect(
        top=-80, bottom=40, left=0, right=100, viewport_w=800, viewport_h=600
    )
    assert relation == "partial"
    assert distance == 80
    assert classify_rect(
        top=-200, bottom=-40, left=0, right=100, viewport_w=800, viewport_h=600
    )[:2] == ("above", 40)
    assert classify_rect(
        top=700, bottom=760, left=0, right=100, viewport_w=800, viewport_h=600
    )[:2] == ("below", 100)


def test_pick_current_prefers_visible_section():
    inbox = Placement("inbox", "Inbox", "section", "visible", 0, 1, 20)
    alias = Placement("search_box", "search box", "alias", "visible", 0, 1, 4)
    assert pick_current([alias, inbox]).id == "inbox"


def test_measure_reports_on_screen_and_nearby_not_full_map():
    awareness = measure_viewport(_Page(_raw()), graph=_graph(), page_id="inbox")
    assert awareness.current_section == "Inbox"
    assert awareness.scroll_y == 400
    text = awareness.summary()
    assert "currently on screen: Inbox" in text
    assert "Header (above, 160px)" in text
    assert "Billing (below, 300px)" in text
    assert "#invoice-secret" not in text
    assert "invoice" not in text
    names = [p.name for p in nearby_offscreen(awareness)]
    assert names == ["Header", "Billing"]
    assert "search box" in text
    assert "composer" not in text


def test_planner_screen_omits_offscreen_alias_dump():
    snap = planner_screen(_Page(_raw()), graph=_graph(), page_id="inbox")
    assert snap.startswith("url=https://example.test/app")
    assert "currently on screen: Inbox" in snap
    assert "#invoice-secret" not in snap
    assert "visible=" not in snap


def test_screen_snapshot_without_viewport_stays_text():
    class Bare:
        url = "https://example.test/app"

        def title(self) -> str:
            return "App"

        def inner_text(self, _sel: str, timeout: int = 0) -> str:
            return "Inbox"

    snap = screen_snapshot(Bare())  # type: ignore[arg-type]
    assert "visible=Inbox" in snap
    assert "currently on screen:" not in snap


def test_scroll_uses_measured_distance():
    page = _Page(_raw())
    bring_alias_into_view(page, _graph(), "inbox", "composer")
    assert page.scrolls
    assert page.scrolls[0]["css"] == "#composer"
    assert page.scrolls[0]["delta"] == 628


def test_dom_state_is_viewport_not_full_inventory():
    state = build_dom_state(_Page(_raw()), page_id="inbox", detailed=True, graph=_graph())
    assert state["currently_on_screen"] == "Inbox"
    assert state["nearby_offscreen"][1]["name"] == "Billing"
    assert state["nearby_offscreen"][1]["direction"] == "below"
    assert state["nearby_offscreen"][1]["distance_px"] == 300
    assert all(el["relation"] in {"visible", "partial"} for el in state["elements"])


def test_planner_narrates_only_the_visible_section():
    prompt = build_prompt(
        page_id="inbox",
        flow_ids=["send_test_message"],
        transcript=["user: what is this"],
        corrections=[],
        knowledge=[],
        persona=Persona(product_name="Acme", agent_name="Guide"),
        screen_context="currently on screen: Inbox\nnearby offscreen: Billing (below, 300px)",
    )
    assert "Narrate only the section currently on screen." in prompt
    assert "Billing (below, 300px)" in prompt


def test_empty_awareness_summary():
    assert ViewportAwareness().summary() == ""
