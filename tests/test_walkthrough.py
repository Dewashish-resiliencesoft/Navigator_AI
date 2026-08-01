"""Walkthrough advance, interrupt resume, and anything_else end policy."""

from __future__ import annotations

from uuid import uuid4

import pytest

from navigator.agent.end_policy import ANYTHING_ELSE, WRAP_UP
from navigator.agent.nodes.planning import planning
from navigator.agent.planner import FlowChoice, HANDOFF_SPOKEN
from navigator.agent.state import CallDeps, initial_state
from navigator.voice.tts import PrintSpeaker


def _walkthrough_deps(site_graph, page, log, tmp_path, choose_flow_fn):
    return CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        product_id="acme",
        archive_dir=tmp_path / "archives",
        chroma_path=tmp_path / "chroma",
        groq_api_key=None,
        choose_flow=choose_flow_fn,
    )


def test_planning_advances_one_walkthrough_step(
    site_graph, page, log, tmp_path, state
):
    state["phase"] = "walkthrough"
    state["walkthrough_flow_id"] = "send_test_message"
    state["walkthrough_step"] = 0
    state["transcript"] = []

    deps = _walkthrough_deps(
        site_graph,
        page,
        log,
        tmp_path,
        lambda **k: (_ for _ in ()).throw(
            AssertionError("choose_flow must not run during walkthrough advance")
        ),
    )
    out = planning(state, deps)
    assert len(out["pending_calls"]) == 1
    assert out["pending_calls"][0].tool == "navigate"
    assert out["walkthrough_step"] == 1
    assert out.get("phase") == "walkthrough"


def test_planning_walkthrough_exhausted_asks_anything_else(
    site_graph, page, log, tmp_path, state
):
    flow_len = len(site_graph.flow("inbox", "send_test_message"))
    state["phase"] = "walkthrough"
    state["walkthrough_flow_id"] = "send_test_message"
    state["walkthrough_step"] = flow_len
    state["transcript"] = []

    deps = _walkthrough_deps(
        site_graph,
        page,
        log,
        tmp_path,
        lambda **k: (_ for _ in ()).throw(AssertionError("no chooser")),
    )
    out = planning(state, deps)
    assert out["phase"] == "anything_else"
    assert out["pending_calls"] == []
    assert ANYTHING_ELSE in out["narration"][0]


def test_interrupt_keeps_walkthrough_step(site_graph, page, log, tmp_path, state):
    saved_step = 2
    state["phase"] = "walkthrough"
    state["walkthrough_flow_id"] = "send_test_message"
    state["walkthrough_step"] = saved_step
    state["transcript"] = ["user: Can you show me how to search for a contact?"]

    def fake(**kwargs) -> FlowChoice:
        return FlowChoice(
            flow_id="search_contact",
            spoken_response="I'll search for a contact.",
        )

    deps = _walkthrough_deps(site_graph, page, log, tmp_path, fake)
    out = planning(state, deps)
    assert out["walkthrough_step"] == saved_step
    assert out.get("phase") == "walkthrough"
    assert [c.tool for c in out["pending_calls"]] == ["fill_field", "click_element"]


def test_anything_else_goodbye_ends(site_graph, page, log, tmp_path, state):
    state["phase"] = "anything_else"
    state["transcript"] = ["user: no thanks, goodbye"]

    deps = _walkthrough_deps(
        site_graph,
        page,
        log,
        tmp_path,
        lambda **k: (_ for _ in ()).throw(AssertionError("no chooser")),
    )
    out = planning(state, deps)
    assert out["phase"] == "ending"
    assert WRAP_UP in out["narration"][0]
    assert out["pending_calls"] == []


def test_anything_else_silence_reask_then_leave(site_graph, page, log, tmp_path):
    state = initial_state(uuid4(), "inbox")
    state["phase"] = "anything_else"
    state["silence_rounds"] = 0
    state["transcript"] = ["user: "]

    deps = _walkthrough_deps(
        site_graph,
        page,
        log,
        tmp_path,
        lambda **k: (_ for _ in ()).throw(AssertionError("no chooser")),
    )
    out = planning(state, deps)
    assert out["phase"] == "anything_else"
    assert out["silence_rounds"] == 1
    assert ANYTHING_ELSE in out["narration"][0]

    state_mid = initial_state(uuid4(), "inbox")
    state_mid["phase"] = "anything_else"
    state_mid["silence_rounds"] = 1
    state_mid["transcript"] = ["user: "]
    out_mid = planning(state_mid, deps)
    assert out_mid["phase"] == "anything_else"
    assert out_mid["silence_rounds"] == 2

    state2 = initial_state(uuid4(), "inbox")
    state2["phase"] = "anything_else"
    state2["silence_rounds"] = 2
    state2["transcript"] = ["user: "]
    out2 = planning(state2, deps)
    assert out2["phase"] == "ending"
    assert WRAP_UP in out2["narration"][0]


def test_anything_else_handoff_stays_in_phase(site_graph, page, log, tmp_path, state):
    state["phase"] = "anything_else"
    state["transcript"] = ["user: show me the billing admin panel"]

    def fake(**kwargs) -> FlowChoice:
        return FlowChoice(flow_id=None, spoken_response="ignored")

    deps = _walkthrough_deps(site_graph, page, log, tmp_path, fake)
    out = planning(state, deps)
    assert out["phase"] == "anything_else"
    assert out["plan"].spoken_response == HANDOFF_SPOKEN
    assert out["pending_calls"] == []


def test_walkthrough_missing_flow_id_raises(site_graph, page, log, tmp_path, state):
    state["phase"] = "walkthrough"
    state["walkthrough_flow_id"] = ""
    state["transcript"] = []

    deps = _walkthrough_deps(
        site_graph,
        page,
        log,
        tmp_path,
        lambda **k: (_ for _ in ()).throw(AssertionError("no chooser")),
    )
    with pytest.raises(RuntimeError, match="walkthrough_flow_id"):
        planning(state, deps)


def test_ending_phase_returns_finished(site_graph, page, log, tmp_path):
    state = initial_state(uuid4(), "inbox")
    state["phase"] = "ending"

    deps = _walkthrough_deps(
        site_graph,
        page,
        log,
        tmp_path,
        lambda **k: (_ for _ in ()).throw(AssertionError("no chooser")),
    )
    out = planning(state, deps)
    assert out.get("finished") is True
    assert out["pending_calls"] == []
