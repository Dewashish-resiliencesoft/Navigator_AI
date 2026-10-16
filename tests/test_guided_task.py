"""Guided Agent — plan, stub apply, progress."""

from __future__ import annotations

import yaml

from navigator.automation.guided_task.apply import (
    apply_guided_plan,
    guided_progress,
    guided_stub_selector,
    is_guided_stub_selector,
    playlist_unbound_guided,
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
    assert len(plan.flows) == 1
    assert any(s.kind == "USER_INPUT" for f in plan.flows for s in f.steps)
    assert any(s.kind == "ACTION" for f in plan.flows for s in f.steps)


def test_structured_flow_headers_merge_to_one_flow():
    """FLOW 1/2/3 blocks merge into one demo flow with a clean title."""
    prompt = """
Create a complete product demo using multiple logical flows.

FLOW 1 — PHONEBOOK / CONTACT SETUP
1. Start from the Phonebook/Contacts section.
2. Ask the user for the phone number they want to use for this demo.
3. Add that phone number as a new contact.

FLOW 2 — INBOX / TWO-WAY MESSAGING
1. Open the Inbox/Messages section.
2. Reuse the previously collected phone_number.
3. Ask the user what message they want to send.

FLOW 3 — SEND CAMPAIGN
1. Navigate to Campaigns.
2. Create a campaign using the collected values.

FLOW 4 — OTP / VERIFICATION
1. Trigger/send the OTP.
2. Ask the user to provide the OTP they received.

IMPORTANT GUIDED-AGENT RULES
- Never invent an OTP
- Prefer multiple small reusable flows over one long flow.
"""
    plan = plan_from_task(prompt)
    assert len(plan.flows) == 1
    name = plan.flows[0].name
    assert "never invent" not in name.lower()
    assert len(plan.flows[0].steps) >= 8
    assert any(s.kind == "USER_INPUT" for s in plan.flows[0].steps)


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
    assert len(graph.demo_playlist) == 1
    calls = graph.pages["dashboard"].flows["contacts_flow"]
    assert len(calls) == 2
    assert calls[0].tool == "fill_field"
    assert getattr(calls[0], "source", None) == "user"
    assert is_guided_stub_selector(graph.pages["dashboard"].selectors["phone_number"])
    assert playlist_unbound_guided(yaml.safe_load(out))


def test_replan_replaces_prior_guided_stubs():
    first = GuidedPlan(
        task_id="gt_a",
        prompt="a",
        flows=(
            GuidedFlow(
                name="Old",
                flow_id="old_flow",
                page_id="dashboard",
                steps=(GuidedStep(kind="ACTION", label="Old", alias="old_btn"),),
            ),
        ),
    )
    mid = apply_guided_plan(_empty_graph(), first)
    second = GuidedPlan(
        task_id="gt_b",
        prompt="b",
        flows=(
            GuidedFlow(
                name="New",
                flow_id="new_flow",
                page_id="dashboard",
                steps=(GuidedStep(kind="ACTION", label="New", alias="new_btn"),),
            ),
        ),
    )
    out = apply_guided_plan(mid, second)
    raw = yaml.safe_load(out)
    flows = raw["pages"]["dashboard"]["flows"]
    assert "new_flow" in flows
    assert "old_flow" not in flows
    assert len(raw["demo_playlist"]) == 1
    assert raw["demo_playlist"][0]["flow_id"] == "new_flow"


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


def test_propose_live_question_fallback_no_page():
    from navigator.automation.guided_task.ask_visitor import propose_live_question

    class _BadPage:
        def screenshot(self, **_kw):
            raise RuntimeError("no display")

    q = propose_live_question(_BadPage(), "ask for their work email")
    assert "email" in q.lower()
    assert q.endswith("?")


def test_mark_ask_visitor_rewrites_step_to_user_input():
    from navigator.automation.guided_task.session import GuidedHandsSession

    plan = GuidedPlan(
        task_id="gt_ask",
        prompt="p",
        flows=(
            GuidedFlow(
                name="Demo",
                flow_id="demo",
                page_id="dashboard",
                steps=(
                    GuidedStep(kind="ACTION", label="Open form", alias="open_form"),
                    GuidedStep(kind="ACTION", label="Save", alias="save_btn"),
                ),
            ),
        ),
    )
    plans_out: list = []

    class _Page:
        url = "https://example.com/form"

        def screenshot(self, **_kw):
            return b""

    sess = GuidedHandsSession(
        plan=plan,
        active=True,
        phase="awaiting_input",
        page=_Page(),
        on_plan_update=lambda p: plans_out.append(p),
    )
    from navigator.automation.guided_task.session import GuidedQuestion

    q = GuidedQuestion(
        qid="q1",
        alias="open_form",
        prompt="I could not find a control",
        kind="pick",
    )
    sess.pending_question = q
    sess.mark_ask_visitor("q1", "ask the visitor for phone number")
    assert sess.plan.flows[0].steps[0].kind == "USER_INPUT"
    assert "phone" in (sess.plan.flows[0].steps[0].live_question or "").lower()
    assert sess.step_index == 1
    assert plans_out
    assert plans_out[0].flows[0].steps[0].kind == "USER_INPUT"


def test_hands_user_input_pauses_not_skips():
    from navigator.automation.guided_task.hands import execute_guided_step
    from navigator.automation.guided_task.session import GuidedHandsSession

    step = GuidedStep(
        kind="USER_INPUT",
        label="Ask phone",
        alias="phone_number",
        live_question="What's your number?",
    )
    class _Page:
        url = "https://example.com/"

    result = execute_guided_step(_Page(), step)
    assert result.get("paused") is True
    assert result.get("reason") == "user_input"

    plan = GuidedPlan(
        task_id="gt_h",
        prompt="p",
        flows=(
            GuidedFlow(
                name="Demo",
                flow_id="demo",
                page_id="dashboard",
                steps=(step, GuidedStep(kind="ACTION", label="Next", alias="next_btn")),
            ),
        ),
    )
    sess = GuidedHandsSession(plan=plan, active=True, phase="acting", page=_Page())
    sess.tick()
    assert sess.phase == "awaiting_input"
    assert sess.pending_question is not None
    qid = sess.pending_question.qid
    sess.answer(qid, skip=True)
    assert sess.step_index == 1
    sess.pause()
    assert sess.phase == "paused"
    sess.barge()
    assert sess.phase == "barged"
    sess.resume()
    assert sess.barged is False


def test_patch_insert_user_input_step():
    """Mirrors PATCH /guided-task/plan insert_at behavior."""
    plan = GuidedPlan(
        task_id="gt_p",
        prompt="p",
        flows=(
            GuidedFlow(
                name="Demo",
                flow_id="demo",
                page_id="dashboard",
                steps=(
                    GuidedStep(kind="ACTION", label="Open", alias="open_btn"),
                    GuidedStep(kind="ACTION", label="Save", alias="save_btn"),
                ),
            ),
        ),
    )
    mid = apply_guided_plan(_empty_graph(), plan)
    steps = list(plan.flows[0].steps)
    steps.insert(
        1,
        GuidedStep(
            kind="USER_INPUT",
            label="Ask email",
            alias="email",
            live_question="Email?",
        ),
    )
    new_plan = GuidedPlan(
        task_id=plan.task_id,
        prompt=plan.prompt,
        flows=(
            GuidedFlow(
                name=plan.flows[0].name,
                flow_id=plan.flows[0].flow_id,
                page_id="dashboard",
                steps=tuple(steps),
            ),
        ),
    )
    out = apply_guided_plan(mid, new_plan)
    raw = yaml.safe_load(out)
    calls = raw["pages"]["dashboard"]["flows"]["demo"]
    assert len(calls) == 3
    assert calls[1]["tool"] == "fill_field"
    assert calls[1].get("source") == "user"


def test_guided_task_client_routes_return_410(tmp_path):
    """Flows UI retired Guided Agent — Client HTTP routes stay as 410 stubs."""
    from test_api import ACME, register
    from test_client_dashboard import _cleanup, _client
    from test_demo_authoring_e2e import _headers

    bundle = _client(tmp_path, client_api_key="nav_test")
    client, prev, registry, log, auth_store = bundle
    try:
        p = register(client, "Acme Inbox", ACME)
        headers = _headers(client, auth_store, p["id"])
        for method, path in (
            ("POST", "/client/api/guided-task/plan"),
            ("GET", "/client/api/guided-task/status"),
            ("POST", "/client/api/guided-task/hands/start"),
            ("POST", "/client/api/guided-task/hands/stop"),
            ("PATCH", "/client/api/guided-task/plan"),
        ):
            r = client.request(method, path, headers=headers, json={})
            assert r.status_code == 410, f"{method} {path} → {r.status_code}: {r.text}"
            assert "manual record" in r.json()["detail"].lower()
    finally:
        _cleanup(bundle, prev)
