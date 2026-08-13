"""Walkthrough advance, interrupt resume, and anything_else end policy."""

from __future__ import annotations

from uuid import uuid4

import pytest

from navigator.agent.end_policy import ANYTHING_ELSE, OFF_TOPIC, WRAP_UP
from navigator.agent.nodes.planning import planning
from navigator.agent.planner import FlowChoice
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
    state["auto_play"] = False

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


_PLAYLIST_GRAPH = """
version: 1
site: acme
base_url: https://example.com/
persona:
  product_name: Acme
  one_liner: test
pages:
  main:
    name: Main
    url: /
    selectors:
      body: body
    flows:
      first:
        - tool: navigate
          page_id: main
          expects: {check: visible, selector: body}
      second:
        - tool: navigate
          page_id: main
          expects: {check: visible, selector: body}
demo_playlist:
  - order: 1
    name: First
    page_id: main
    flow_id: first
  - order: 2
    name: Second
    page_id: main
    flow_id: second
"""


def test_auto_play_advances_to_next_playlist_flow(log, tmp_path, state):
    from navigator.knowledge.site_graph import parse_site_graph

    graph = parse_site_graph(_PLAYLIST_GRAPH)
    state["phase"] = "walkthrough"
    state["page_id"] = "main"
    state["walkthrough_page_id"] = "main"
    state["walkthrough_flow_id"] = "first"
    state["walkthrough_step"] = 1  # exhausted first (1 step)
    state["transcript"] = []
    state["auto_play"] = True

    deps = CallDeps(
        graph=graph,
        page=None,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        product_id="acme",
        archive_dir=tmp_path / "archives",
        chroma_path=tmp_path / "chroma",
        groq_api_key=None,
        choose_flow=lambda **k: (_ for _ in ()).throw(AssertionError("no chooser")),
    )
    out = planning(state, deps)
    assert out.get("phase") == "walkthrough"
    assert out["walkthrough_flow_id"] == "second"
    assert out["walkthrough_step"] == 1
    assert len(out["pending_calls"]) == 1


def test_auto_play_off_stops_at_flow_end(log, tmp_path, state):
    from navigator.knowledge.site_graph import parse_site_graph

    graph = parse_site_graph(_PLAYLIST_GRAPH)
    state["phase"] = "walkthrough"
    state["page_id"] = "main"
    state["walkthrough_page_id"] = "main"
    state["walkthrough_flow_id"] = "first"
    state["walkthrough_step"] = 1
    state["transcript"] = []
    state["auto_play"] = False

    deps = CallDeps(
        graph=graph,
        page=None,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        product_id="acme",
        archive_dir=tmp_path / "archives",
        chroma_path=tmp_path / "chroma",
        groq_api_key=None,
        choose_flow=lambda **k: (_ for _ in ()).throw(AssertionError("no chooser")),
    )
    out = planning(state, deps)
    assert out["phase"] == "anything_else"
    assert out["pending_calls"] == []


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
    assert out.get("phase") == "detour"
    assert out.get("detour_flow_id") == "search_contact"
    assert len(out["pending_calls"]) == 1


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


def test_anything_else_silence_leaves_immediately(site_graph, page, log, tmp_path):
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
    assert out["phase"] == "ending"
    assert WRAP_UP in out["narration"][0]
    assert out["pending_calls"] == []


def test_anything_else_off_topic_stays_in_phase(site_graph, page, log, tmp_path, state):
    state["phase"] = "anything_else"
    state["transcript"] = ["user: what's the weather in paris"]

    def fake(**kwargs) -> FlowChoice:
        return FlowChoice(flow_id=None, spoken_response="ignored")

    deps = _walkthrough_deps(site_graph, page, log, tmp_path, fake)
    out = planning(state, deps)
    assert out["phase"] == "anything_else"
    assert out["plan"].spoken_response == OFF_TOPIC
    assert out["pending_calls"] == []


def test_anything_else_knowledge_voice_only(site_graph, page, log, tmp_path, state, monkeypatch):
    state["phase"] = "anything_else"
    state["transcript"] = ["user: how do whatsapp campaigns work"]

    def fake(**kwargs) -> FlowChoice:
        return FlowChoice(flow_id=None, spoken_response="ignored")

    monkeypatch.setattr(
        "navigator.agent.nodes.planning.retrieve_product_knowledge",
        lambda *a, **k: ["Campaigns let you broadcast to opted-in contacts."],
    )
    deps = _walkthrough_deps(site_graph, page, log, tmp_path, fake)
    out = planning(state, deps)
    assert out["phase"] == "anything_else"
    assert out["pending_calls"] == []
    assert "broadcast" in out["narration"][0].lower()


def test_anything_else_flow_drives_ui(site_graph, page, log, tmp_path, state):
    state["phase"] = "anything_else"
    state["transcript"] = ["user: show me how to search for a contact"]

    def fake(**kwargs) -> FlowChoice:
        return FlowChoice(
            flow_id="search_contact",
            spoken_response="Sure, let me show you search.",
        )

    deps = _walkthrough_deps(site_graph, page, log, tmp_path, fake)
    out = planning(state, deps)
    assert out["phase"] == "anything_else"
    assert out["pending_calls"]
    assert "search" in out["narration"][0].lower()


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
