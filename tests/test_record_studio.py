"""Manual record studio: ask-visitor vars + value_ref + draft meta."""

from __future__ import annotations

from navigator.agent.live_input import needs_live_input, resolve_demo_fill
from navigator.automation.record import RecordedStep, draft_site_graph, guess_postcondition
from navigator.automation.record_studio import (
    bind_value_ref,
    mark_step_replay_recorded_value,
    demo_variables_from_steps,
    mark_step_ask_visitor,
)
from navigator.core.schemas import FillField, Postcondition


def test_mark_ask_visitor_clears_typed_value_and_sets_source_user():
    steps = [
        RecordedStep(
            tool="fill_field",
            alias="email",
            selector="#email",
            value="demo@example.com",
        )
    ]
    step = mark_step_ask_visitor(
        steps,
        var_alias="work_email",
        live_question="What's your work email?",
        page=None,
    )
    assert step.source == "user"
    assert step.value == "{{work_email}}"
    assert step.fallback_value == "demo@example.com"
    assert step.input_name == "work_email"
    assert step.alias == "work_email"
    assert "email" in (step.live_question or "").lower()
    assert demo_variables_from_steps(steps)[0]["alias"] == "work_email"


def test_ask_visitor_compiles_to_interaction_schema():
    from navigator.agent_runtime.demo_compiler import compile_step
    from navigator.agent_runtime.models import InteractionMode

    step = RecordedStep(tool="fill_field", alias="phone_field", selector="#phone", value="5550102000")
    mark_step_ask_visitor(steps := [step], var_alias="phone", input_type="phone", live_question="What phone number should I use?")
    compiled = compile_step(steps[0])
    assert compiled.action.value == "{{phone}}"
    assert compiled.interaction.mode == InteractionMode.ask
    assert compiled.interaction.input_name == "phone"
    assert compiled.interaction.input_type == "phone"
    assert compiled.interaction.fallback_value == "5550102000"


def test_replay_recorded_value_restores_noninteractive_field():
    steps = [RecordedStep(tool="fill_field", alias="email", selector="#email", value="sample@acme.test")]
    mark_step_ask_visitor(steps, var_alias="email", live_question="Email?")
    restored = mark_step_replay_recorded_value(steps)
    assert restored.source == "agent"
    assert restored.value == "sample@acme.test"
    assert restored.input_name is None


def test_draft_persists_source_user_and_demo_variables():
    steps = [
        RecordedStep(
            tool="fill_field",
            alias="work_email",
            selector="#email",
            value="",
            source="user",
            live_question="What's your work email?",
        )
    ]
    steps[0].postcondition = guess_postcondition(steps[0])
    draft = draft_site_graph(
        base_url="https://example.com", product_name="Demo", steps=steps
    )
    call = draft["pages"]["main"]["flows"]["recorded_demo"][0]
    assert call["source"] == "user"
    assert call["value"] == ""
    assert "fallback_value" not in call
    assert call["input_name"] == "work_email"
    assert call["input_type"] == "text"
    assert call["live_question"]
    assert draft["_meta"]["demo_variables"][0]["alias"] == "work_email"
    # Visitor fill must not assert empty value_equals.
    assert call["expects"]["check"] == "visible"


def test_bind_value_ref_and_parse_fill_field():
    steps = [
        RecordedStep(
            tool="fill_field",
            alias="work_email",
            selector="#email",
            source="user",
            live_question="Email?",
            value="",
        ),
        RecordedStep(
            tool="fill_field",
            alias="campaign_to",
            selector="#to",
            value="typed-demo",
        ),
    ]
    bind_value_ref(steps, step_index=1, value_ref="work_email")
    assert steps[1].value_ref == "work_email"
    assert steps[1].value == ""
    assert steps[1].source == "agent"
    draft = draft_site_graph(
        base_url="https://example.com", product_name="Demo", steps=steps
    )
    call = draft["pages"]["main"]["flows"]["recorded_demo"][1]
    assert call["value_ref"] == "work_email"
    fill = FillField.model_validate(
        {
            "tool": "fill_field",
            "selector": "campaign_to",
            "value": "",
            "value_ref": "work_email",
            "expects": {"check": "visible", "selector": "campaign_to"},
        }
    )
    assert fill.value_ref == "work_email"


def test_resolve_value_ref_reuses_prior_answer():
    answers: dict[str, str] = {"work_email": "a@b.com"}
    call = FillField(
        selector="campaign_to",
        value="",
        value_ref="work_email",
        expects=Postcondition(
            check="visible", selector="campaign_to", timeout_ms=1000
        ),
    )
    assert needs_live_input(call) is False
    updated, detail = resolve_demo_fill(
        call,
        live_answers=answers,
        listen_once=lambda _p: "should-not-run",
        extract_entity=None,
        speak=None,
    )
    assert updated.value == "a@b.com"
    assert "value_ref" in detail


def test_resolve_user_stores_answer_for_later_ref():
    answers: dict[str, str] = {}
    call = FillField(
        selector="work_email",
        value="fallback",
        source="user",
        live_question="Email?",
        expects=Postcondition(
            check="visible", selector="work_email", timeout_ms=1000
        ),
    )
    updated, _ = resolve_demo_fill(
        call,
        live_answers=answers,
        listen_once=lambda _p: "visitor@co.com",
        extract_entity=None,
        speak=None,
    )
    assert updated.value == "visitor@co.com"
    assert answers["work_email"] == "visitor@co.com"


def test_draft_persists_scroll_page():
    steps = [
        RecordedStep(
            tool="scroll_page",
            alias="window",
            selector="body",
            value="0,840",
        )
    ]
    steps[0].postcondition = guess_postcondition(steps[0])
    draft = draft_site_graph(
        base_url="https://example.com", product_name="Demo", steps=steps
    )
    call = draft["pages"]["main"]["flows"]["recorded_demo"][0]
    assert call["tool"] == "scroll_page"
    assert call["x"] == 0
    assert call["y"] == 840


def test_scroll_page_schema_parses():
    from navigator.core.schemas import Postcondition, ScrollPage, parse_tool_call

    call = parse_tool_call(
        {
            "tool": "scroll_page",
            "x": 0,
            "y": 400,
            "expects": {"check": "visible", "selector": "body"},
        }
    )
    assert isinstance(call, ScrollPage)
    assert call.y == 400
