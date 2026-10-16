"""Viewport section awareness: on-screen name and nearby off-screen list."""

from __future__ import annotations

from navigator.agent.planner import build_prompt
from navigator.agent_runtime.dom.builder import build_dom_state
from navigator.automation.browser.screen_context import planner_screen, screen_snapshot
from navigator.automation.browser.viewport_awareness import (
    Placement,
    ViewportAwareness,
    begin_viewport_step,
    bring_alias_into_view,
    end_viewport_step,
    classify_in_view,
    classify_rect,
    header_offset_px,
    measure_viewport,
    nearby_offscreen,
    pick_current,
    scroll_toward_label,
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
        self.reads = 0

    def title(self) -> str:
        return "App"

    def evaluate(self, _js, arg=None, **_k):
        if isinstance(arg, dict) and "delta" in arg:
            self.scrolls.append(arg)
            return True
        self.reads += 1
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


def test_clipped_box_inside_the_window_is_not_visible():
    """An overflow ancestor hides the box even when the window still contains it."""
    relation, _distance, _ratio = classify_in_view(
        top=400,
        bottom=440,
        left=0,
        right=120,
        viewport_w=800,
        viewport_h=900,
        clip_top=180,
        clip_bottom=400,
        clip_left=0,
        clip_right=800,
    )
    assert relation == "below"
    assert classify_rect(
        top=400, bottom=440, left=0, right=120, viewport_w=800, viewport_h=900
    )[0] == "visible"


def test_nested_scroller_does_not_use_the_window_header():
    raw = _raw()
    raw["measured"][1] = {
        **raw["measured"][1],
        "rawTop": 700,
        "containerTop": 200,
    }
    page = _Page(raw)
    bring_alias_into_view(page, _graph(), "inbox", "composer")
    assert page.scrolls[0]["delta"] == 500


def test_header_offset_comes_from_the_site_graph():
    graph = _graph().model_copy(update={"meta": {"header_offset_px": 0}})
    assert header_offset_px(graph) == 0
    page = _Page(_raw())
    bring_alias_into_view(page, graph, "inbox", "composer")
    assert page.scrolls[0]["delta"] == 700


def test_section_css_is_the_matched_heading():
    raw = _raw()
    raw["headings"][0] = {**raw["headings"][0], "css": "#inbox-heading"}
    awareness = measure_viewport(_Page(raw), graph=_graph(), page_id="inbox")
    section = next(p for p in awareness.placements if p.id == "inbox")
    assert section.css == "#inbox-heading"


def test_a_second_page_does_not_reuse_another_pages_reading():
    """A step left open must not serve page A's reading to page B.

    The cache used to key on ``id(page)``. CPython reuses that id after the
    first page is freed, so a later scroll applied the wrong container.
    """
    begin_viewport_step()
    try:
        first = _Page(_raw())
        measure_viewport(first, graph=_graph(), page_id="inbox")
        freed = id(first)
        del first
        raw = _raw()
        raw["measured"][1] = {
            **raw["measured"][1],
            "rawTop": 700,
            "containerTop": 200,
        }
        second = None
        for _ in range(64):
            candidate = _Page(raw)
            if id(candidate) == freed:
                second = candidate
                break
        assert second is not None
        bring_alias_into_view(second, _graph(), "inbox", "composer")
        assert second.scrolls[0]["delta"] == 500
    finally:
        end_viewport_step()


def test_viewport_is_read_once_per_step():
    page = _Page(_raw())
    begin_viewport_step()
    try:
        first = measure_viewport(page, graph=_graph(), page_id="inbox")
        second = measure_viewport(page, graph=_graph(), page_id="inbox")
        assert page.reads == 1
        assert second.current_section == first.current_section
        begin_viewport_step()
        measure_viewport(page, graph=_graph(), page_id="inbox")
        assert page.reads == 2
    finally:
        end_viewport_step()


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
    assert "only map" in prompt
    assert "Do not describe the rest of the site." in prompt
    assert "Billing (below, 300px)" in prompt


def test_empty_awareness_summary():
    assert ViewportAwareness().summary() == ""


def test_live_clip_scrolls_inside_the_nested_pane(browser):
    """A heading inside the window but outside a nested pane is off screen.

    Scrolling it uses the pane, and stops at the pane's top rather than the
    window header. The selector is the heading, not the page's first alias.
    """
    context = browser.new_context(viewport={"width": 800, "height": 900})
    page = context.new_page()
    try:
        page.set_content(
            """
            <style>
              body { margin: 0; }
              #pad { height: 180px; }
              #pane { height: 220px; overflow: auto; }
              #spacer { height: 500px; }
              #billing { margin: 0; height: 40px; }
            </style>
            <div id="pad"></div>
            <div id="pane">
              <div id="spacer"></div>
              <h2 id="billing">Billing</h2>
              <div style="height:200px"></div>
            </div>
            """,
            wait_until="domcontentloaded",
        )
        graph = SiteGraph(
            version=1,
            site="acme",
            base_url="https://example.test",
            pages={
                "billing": PageSpec(
                    name="Billing",
                    url="/billing",
                    selectors={"first_alias": "#not-the-heading"},
                )
            },
        )
        awareness = measure_viewport(page, graph=graph, page_id="billing")
        billing = next(p for p in awareness.placements if p.name == "Billing")
        assert billing.relation != "visible"
        assert billing.css == "#billing"
        pane_top = page.evaluate(
            "() => document.querySelector('#pane').getBoundingClientRect().top"
        )
        assert scroll_toward_label(page, graph, "billing", "Billing") is True
        after = page.evaluate(
            "() => document.querySelector('#billing').getBoundingClientRect().top"
        )
        assert after >= pane_top - 4
    finally:
        context.close()
