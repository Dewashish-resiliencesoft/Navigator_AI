"""VLM visual targeting — guardrail gate is the critical invariant."""

from __future__ import annotations

from navigator.automation.explore import visual_target
from navigator.automation.explore.repair import tactics_for


def test_parse_coords_ok():
    assert visual_target.parse_coords('{"x": 500, "y": 200, "found": true}') == (500, 200)


def test_parse_coords_not_found():
    assert visual_target.parse_coords('{"found": false}') is None


def test_parse_coords_garbage():
    assert visual_target.parse_coords("nope") is None


def test_element_at_point_picks_smallest_box():
    els = [
        {"testid": "page", "box": {"x": 0, "y": 0, "w": 1000, "h": 800}},
        {"testid": "btn", "box": {"x": 100, "y": 100, "w": 80, "h": 40}},
    ]
    # Normalized 500,200 on 1280x720 → px ~640, 144 — wait, let's use known numbers.
    # x_norm=100 → 128px on 1280; y_norm=150 → 108 on 720. Button covers 100-180, 100-140.
    hit = visual_target.element_at_point(
        els, x_norm=int(120 / 1280 * 1000), y_norm=int(120 / 720 * 1000), viewport=(1280, 720)
    )
    assert hit is not None
    assert hit["testid"] == "btn"


def test_flagged_element_not_clicked():
    """Critical: VLM hit still goes through classify_action; flagged → no click."""
    el = {
        "tag": "button",
        "testid": "delete",
        "text": "Delete forever",
        "box": {"x": 10, "y": 10, "w": 100, "h": 40},
    }
    page = type("P", (), {})()

    def ask(_prompt: str, _b64: str) -> str:
        return '{"x": 50, "y": 50, "found": true}'

    hit = visual_target.locate(
        page=page,
        target="Delete forever",
        ask_vision=ask,
        guard_judge=lambda _p: '{"destructive": true, "reason": "delete"}',
        is_allowed=lambda _el, _css: False,
        inventory=lambda _p: [el],
        screenshot=lambda _p: "abc",
        viewport=(200, 200),
    )
    assert hit is None


def test_no_provider_no_click():
    assert (
        visual_target.locate(
            page=object(),
            target="x",
            ask_vision=None,
            guard_judge=None,
            is_allowed=lambda *_a: True,
            inventory=lambda _p: [],
            screenshot=lambda _p: "x",
        )
        is None
    )


def test_vlm_locate_is_last_for_not_found():
    ordered = tactics_for("not_found")
    assert ordered[-1] == "vlm_locate"
    assert ordered.index("alternate_selector") < ordered.index("vlm_locate")


def test_max_vlm_budget_skips_tactic():
    from navigator.automation.explore.repair import RepairAttempt, RepairCtx, run_ladder
    from navigator.core.schemas import ToolResult

    calls = {"vision": 0}

    def ask(_p: str, _b: str) -> str:
        calls["vision"] += 1
        return '{"found": false}'

    ctx = RepairCtx(
        page=object(),
        graph=type("G", (), {"add": lambda *a, **k: None})(),
        page_id="main",
        el={"tag": "button", "testid": "x", "text": "X"},
        alias="x",
        css="#x",
        fillable=False,
        value=None,
        execute=lambda *_a: (
            ToolResult(ok=False, tool="click_element", detail="missing", duration_ms=1),
            "main",
        ),
        verify=lambda *_a: None,
        guard_judge=None,
        is_allowed=lambda *_a: True,
        max_repairs=5,
        inventory=lambda _p: [],
        ask_vision=ask,
        vlm_locates_left=0,
    )
    outcome = run_ladder(ctx, "not_found")
    assert calls["vision"] == 0
    assert "vlm_locate" not in outcome.tactics_tried
