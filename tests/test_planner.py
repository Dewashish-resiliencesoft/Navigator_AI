"""LLM flow picker: injectable choose_flow + planning orchestration."""

from __future__ import annotations

from uuid import uuid4

import pytest

from navigator.agent.nodes.planning import planning
from navigator.agent.planner import FlowChoice, choose_flow, parse_flow_choice
from navigator.agent.state import CallDeps, initial_state
from navigator.schemas import Persona
from navigator.voice.tts import PrintSpeaker


def test_calldeps_accepts_planner_fields(site_graph, page, log, tmp_path):
    def fake(**kwargs) -> FlowChoice:
        return FlowChoice(flow_id="send_test_message", spoken_response="ok")

    deps = CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        product_id="acme",
        archive_dir=tmp_path / "archives",
        groq_api_key=None,
        chroma_path=tmp_path / "chroma",
        choose_flow=fake,
    )
    assert deps.choose_flow is fake
    assert deps.chroma_path == tmp_path / "chroma"


def test_parse_flow_choice_accepts_valid_json():
    choice = parse_flow_choice(
        '{"flow_id": "send_test_message", "spoken_response": "Let me show send."}',
        allowed={"send_test_message", "search_contact"},
    )
    assert choice.flow_id == "send_test_message"


def test_parse_flow_choice_rejects_unknown_flow():
    with pytest.raises(ValueError, match="not in allowed"):
        parse_flow_choice(
            '{"flow_id": "nope", "spoken_response": "x"}',
            allowed={"send_test_message"},
        )


def test_parse_flow_choice_accepts_null_handoff():
    choice = parse_flow_choice(
        '{"flow_id": null, "spoken_response": "cannot show that"}',
        allowed={"send_test_message"},
    )
    assert choice.flow_id is None


def test_parse_flow_choice_accepts_handoff_token():
    choice = parse_flow_choice(
        '{"flow_id": "__handoff__", "spoken_response": "x"}',
        allowed={"send_test_message"},
    )
    assert choice.flow_id is None


def test_choose_flow_retries_once_then_raises():
    calls: list[str] = []

    def fake_complete(prompt: str) -> str:
        calls.append(prompt)
        return '{"flow_id": "nope", "spoken_response": "x"}'

    with pytest.raises(ValueError, match="not in allowed"):
        choose_flow(
            api_key="unused",
            page_id="inbox",
            flow_ids=["send_test_message"],
            transcript=["user: show send"],
            corrections=[],
            knowledge=[],
            persona=Persona(product_name="Demo"),
            complete=fake_complete,
        )
    assert len(calls) == 2


def _llm_deps(site_graph, page, log, tmp_path, choose_flow_fn, product_id="acme"):
    return CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        product_id=product_id,
        archive_dir=tmp_path / "archives",
        chroma_path=tmp_path / "chroma",
        choose_flow=choose_flow_fn,
    )


def test_planning_scripted_flow_still_wins(state, deps):
    out = planning(state, deps)
    assert [c.tool for c in out["pending_calls"]] == [
        "navigate",
        "wait_for",
        "fill_field",
        "click_element",
    ]


def test_planning_uses_choose_flow_and_expands_graph_flow(
    site_graph, page, log, tmp_path, state
):
    def fake(**kwargs) -> FlowChoice:
        assert kwargs["page_id"] == "inbox"
        assert "send_test_message" in kwargs["flow_ids"]
        return FlowChoice(
            flow_id="search_contact",
            spoken_response="I'll search for a contact.",
        )

    state["phase"] = "anything_else"
    state["transcript"] = ["user: show me how to search for a contact"]
    deps = _llm_deps(site_graph, page, log, tmp_path, fake)
    out = planning(state, deps)
    assert out["plan"].spoken_response == "I'll search for a contact."
    expected = list(site_graph.flow("inbox", "search_contact"))
    assert out["plan"].tool_calls == expected
    assert [c.tool for c in out["pending_calls"]] == ["fill_field", "click_element"]


def test_planning_rejects_unknown_flow_from_chooser(
    site_graph, page, log, tmp_path, state
):
    def fake(**kwargs) -> FlowChoice:
        return FlowChoice(flow_id="does_not_exist", spoken_response="x")

    state["phase"] = "anything_else"
    state["transcript"] = ["user: show me something weird"]
    deps = _llm_deps(site_graph, page, log, tmp_path, fake)
    with pytest.raises(ValueError, match="does_not_exist"):
        planning(state, deps)


def test_planning_handoff_emits_empty_tools_and_fixed_spoken(
    site_graph, page, log, tmp_path, state
):
    from navigator.agent.planner import HANDOFF_SPOKEN

    def fake(**kwargs) -> FlowChoice:
        return FlowChoice(flow_id=None, spoken_response="ignored")

    state["transcript"] = ["user: show me the billing admin panel"]
    deps = _llm_deps(site_graph, page, log, tmp_path, fake)
    out = planning(state, deps)
    assert out["plan"].tool_calls == []
    assert out["pending_calls"] == []
    assert out["plan"].spoken_response == HANDOFF_SPOKEN
    assert "confidential" in out["narration"][0].lower()


def test_planning_user_correction_logs_pending_no_tools(
    site_graph, page, log, tmp_path, state
):
    state["transcript"] = ["user: no, you should wait for the toast first"]
    state["user_correction"] = True
    deps = _llm_deps(site_graph, page, log, tmp_path, lambda **k: None)
    deps.pending_db_path = tmp_path / "pending.db"
    out = planning(state, deps)
    assert out["pending_calls"] == []
    assert "noted that correction" in out["plan"].spoken_response.lower()


def test_planning_requires_key_without_scripted_or_chooser(
    site_graph, page, log, tmp_path, state, monkeypatch
):
    monkeypatch.setattr("navigator.agent.nodes.planning.settings.groq_api_key", "")
    deps = CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        archive_dir=tmp_path / "archives",
        chroma_path=tmp_path / "chroma",
        groq_api_key=None,
        choose_flow=None,
    )
    with pytest.raises(RuntimeError, match="scripted_flow"):
        planning(state, deps)


def test_planning_passes_retrieved_corrections_into_chooser(
    site_graph, page, log, tmp_path, state
):
    from navigator.memory.seed import seed_correction

    path = tmp_path / "chroma"
    seed_correction(
        path,
        product_id="acme",
        rule="Always wait for composer before send",
        page="inbox",
        tool_call_type="click_element",
        source_call_id="c1",
    )
    seen: dict = {}

    def fake(**kwargs) -> FlowChoice:
        seen.update(kwargs)
        return FlowChoice(
            flow_id="send_test_message",
            spoken_response="Sending a message.",
        )

    state = initial_state(uuid4(), "inbox")
    state["transcript"] = ["user: Can you show me how sending a message works?"]
    deps = _llm_deps(site_graph, page, log, tmp_path, fake)
    planning(state, deps)
    assert any("composer" in c.rule for c in seen.get("corrections", [])), seen.get(
        "corrections"
    )


def test_build_prompt_includes_intake_looking_for():
    from navigator.agent.planner import build_prompt
    from navigator.meeting.intake import ProspectIntake

    persona = Persona(
        agent_name="Nav",
        product_name="CRM",
        tone="warm",
        one_liner="inbox that works",
    )
    intake = ProspectIntake(
        name="Ada",
        company="Acme",
        business_type="retail",
        looking_for="broadcast campaigns",
    )
    prompt = build_prompt(
        page_id="inbox",
        flow_ids=["send_test_message"],
        transcript=["user: hi"],
        corrections=[],
        knowledge=[],
        persona=persona,
        intake=intake,
    )
    assert "Acme" in prompt
    assert "broadcast campaigns" in prompt
    assert "Prospect intake" in prompt


def test_planning_passes_intake_into_chooser(
    site_graph, page, log, tmp_path, state
):
    from navigator.meeting.intake import ProspectIntake

    seen: dict = {}

    def fake(**kwargs) -> FlowChoice:
        seen.update(kwargs)
        return FlowChoice(
            flow_id="send_test_message",
            spoken_response="Sending a message.",
        )

    intake = ProspectIntake(
        name="Ada",
        company="Acme",
        looking_for="broadcast campaigns",
    )
    state["phase"] = "anything_else"
    state["transcript"] = ["user: show broadcast campaigns"]
    deps = _llm_deps(site_graph, page, log, tmp_path, fake)
    deps.intake = intake
    planning(state, deps)
    assert seen.get("intake") is intake
