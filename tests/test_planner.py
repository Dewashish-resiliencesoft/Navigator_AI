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

    deps = _llm_deps(site_graph, page, log, tmp_path, fake)
    with pytest.raises(ValueError, match="does_not_exist"):
        planning(state, deps)


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
