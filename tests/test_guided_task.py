"""Guided Agent — plan, stub apply, progress."""

from __future__ import annotations

import yaml

from navigator.automation.guided_task.apply import (
    apply_guided_plan,
    guided_progress,
    guided_stub_selector,
    is_guided_stub_selector,
)
from navigator.automation.guided_task.models import GuidedFlow, GuidedPlan, GuidedStep
from navigator.automation.guided_task.planner import plan_from_task
from navigator.knowledge.site_graph import parse_site_graph


def _empty_graph() -> str:
    return yaml.safe_dump(
        {
            "version": 1,
            "site": "test_product",
            "base_url": "https://app.example.com/",
            "pages": {
                "dashboard": {
                    "name": "Dashboard",
                    "url": "/",
                    "selectors": {"body": "body"},
                    "flows": {},
                }
            },
            "demo_playlist": [],
        }
    )


def test_heuristic_plan_splits_task():
    plan = plan_from_task(
        "Ask for phone number. Click create tag. Add contact. Open pipeline."
    )
    assert plan.flows
    assert any(s.kind == "USER_INPUT" for f in plan.flows for s in f.steps)
    assert any(s.kind == "ACTION" for f in plan.flows for s in f.steps)


def test_apply_guided_plan_writes_stubs_and_playlist():
    plan = GuidedPlan(
        task_id="gt_test",
        prompt="demo",
        flows=(
            GuidedFlow(
                name="Contacts",
                flow_id="contacts_flow",
                page_id="dashboard",
                steps=(
                    GuidedStep(
                        kind="USER_INPUT",
                        label="Ask phone",
                        alias="phone_number",
                        live_question="What's your phone number?",
                    ),
                    GuidedStep(
                        kind="ACTION",
                        label="Create tag",
                        alias="create_tag_btn",
                        action_hint="create tag",
                    ),
                ),
            ),
        ),
    )
    out = apply_guided_plan(_empty_graph(), plan)
    graph = parse_site_graph(out)
    assert graph.demo_playlist
    calls = graph.pages["dashboard"].flows["contacts_flow"]
    assert len(calls) == 2
    assert calls[0].tool == "fill_field"
    assert getattr(calls[0], "source", None) == "user"
    assert is_guided_stub_selector(graph.pages["dashboard"].selectors["phone_number"])


def test_guided_progress_zero_until_bound():
    plan = GuidedPlan(
        task_id="gt_test2",
        prompt="demo",
        flows=(
            GuidedFlow(
                name="One",
                flow_id="one_flow",
                page_id="dashboard",
                steps=(GuidedStep(kind="ACTION", label="Go", alias="go_btn"),),
            ),
        ),
    )
    out = apply_guided_plan(_empty_graph(), plan)
    raw = yaml.safe_load(out)
    prog = guided_progress(raw)
    assert prog["steps_total"] == 1
    assert prog["steps_bound"] == 0


def test_guided_stub_selector_marker():
    css = guided_stub_selector("phone_number")
    assert is_guided_stub_selector(css)
    assert "phone_number" in css
