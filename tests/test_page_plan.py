"""Page plan parsing and no-vision fallback."""

from __future__ import annotations

from navigator.automation.explore import page_plan


def test_parse_plan_orders_actions_and_caps_commit():
    raw = """
    {"purpose": "Contacts — add customers",
     "actions": [
       {"index": 0, "kind": "click", "narration": "Open create"},
       {"index": 1, "kind": "fill", "narration": "Type a name"},
       {"index": 2, "kind": "commit", "narration": "Save"},
       {"index": 3, "kind": "commit", "narration": "Duplicate save"}
     ]}
    """
    plan = page_plan.parse_plan(raw, 4)
    assert plan.purpose.startswith("Contacts")
    assert len(plan.actions) == 3
    assert plan.actions[-1].kind == "commit"


def test_parse_plan_malformed_json_yields_empty():
    assert page_plan.parse_plan("not json at all", 3) == page_plan.PagePlan()
    assert page_plan.parse_plan('{"actions": [{"index": 99}]}', 2) == page_plan.PagePlan()


def test_plan_page_without_vision_returns_empty():
    els = [{"tag": "button", "text": "Add", "fillable": False}]
    plan = page_plan.plan_page(
        url="https://app.example.com/contacts",
        elements=els,
        screenshot_b64="",
        ask_vision=None,
        ask_text=None,
    )
    assert not plan
