"""Nodes are independently testable, and the whole scripted loop runs end to end."""

from __future__ import annotations

from uuid import uuid4

from navigator.agent.graph import after_speaking, after_turn, build_graph
from navigator.agent.nodes.executing import executing
from navigator.agent.nodes.introducing import introducing, render_intro
from navigator.agent.nodes.planning import planning
from navigator.agent.nodes.speaking import speaking
from navigator.agent.nodes.verifying import verifying
from navigator.agent.state import initial_state, queue

# --- individual nodes, no graph ----------------------------------------------


def test_introducing_needs_no_llm_and_no_browser(state, deps):
    out = introducing(state, deps)
    assert out["narration"] == [render_intro(deps.graph.effective_persona())]
    assert out["transcript"][0].startswith("agent:")


def test_intro_names_the_product_from_its_persona(state, deps):
    """No product name is hardcoded in the node -- it comes from the site graph."""
    (line,) = introducing(state, deps)["narration"]
    assert "WhatsApp CRM dashboard" in line
    assert "shared inbox for sales teams" in line


def test_intro_renders_personalized_with_intake():
    from navigator.meeting.intake import ProspectIntake
    from navigator.core.schemas import Persona

    line = render_intro(
        Persona(product_name="ResilioHub", one_liner="WhatsApp CRM", agent_name="Navigator"),
        ProspectIntake(name="Dewa", company="Acme", looking_for="shared inbox"),
    )
    assert "Dewa" in line
    assert "shared inbox" in line or "inbox" in line.lower()
    assert "Acme" in line


def test_intro_does_not_dump_raw_stt_ramble():
    from navigator.meeting.intake import ProspectIntake
    from navigator.core.schemas import Persona

    ramble = (
        "Yeah, actually we need like we have a sharp quiz app, which is a quiz "
        "game. We need WhatsApp CRM support for that"
    )
    line = render_intro(
        Persona(product_name="ResilioHub", one_liner="WhatsApp CRM", agent_name="Navigator"),
        ProspectIntake(name="Dewashish", company="ResilientSoft", looking_for=ramble),
    )
    assert "Yeah, actually" not in line
    assert "Dewashish" in line
    assert "ResilientSoft" in line


def test_planning_replays_the_scripted_flow(state, deps):
    out = planning(state, deps)
    assert [c.tool for c in out["pending_calls"]] == [
        "navigate",
        "wait_for",
        "fill_field",
        "click_element",
    ]
    assert out["plan"].spoken_response == out["narration"][0]


def test_executing_takes_exactly_one_call(state, deps):
    state.update(planning(state, deps))
    before = len(state["pending_calls"])

    out = executing(state, deps)
    assert len(out["pending_calls"]) == before - 1
    assert out["last_call"].tool == "navigate"
    assert out["last_result"].ok


def test_executing_on_empty_plan_is_a_noop(state, deps):
    out = executing(state, deps)
    assert out["last_call"] is None


def test_verifying_logs_a_passing_entry(state, deps):
    state.update(planning(state, deps))
    state.update(executing(state, deps))
    out = verifying(state, deps)

    (entry,) = out["entries"]
    assert entry.verify.passed
    assert out["failures"] == []
    assert deps.log.entries(state["session_id"])[0] == entry


def test_verifying_logs_a_failure_and_narrates_softly(state, deps):
    """Prospect hears a soft apology — never Playwright jargon."""
    state.update(planning(state, deps))
    state.update(executing(state, deps))  # navigate, so the page is loaded
    deps.page.evaluate("document.querySelector('#message-input').remove()")

    # Skip to the fill step, whose postcondition can no longer hold.
    state["pending_calls"] = state["pending_calls"][1:]
    state.update(executing(state, deps))
    out = verifying(state, deps)

    (entry,) = out["failures"]
    assert entry.failed
    line = out["narration"][0]
    assert "on our side" in line or "not yours" in line or "Nothing you did" in line or "nothing you did" in line
    assert "Page." not in line
    assert "Timeout" not in line
    assert "action failed" not in line
    assert deps.log.failures(state["session_id"]) == [entry]


def test_speaking_drains_the_queue(state, deps):
    state["narration"] = ["one", "two"]
    out = speaking(state, deps)
    assert deps.speaker.said == ["one", "two"]
    assert queue(["one", "two"], out["narration"]) == [], "must clear, not accumulate"


def test_narration_from_two_nodes_is_not_lost(state, deps):
    """The queue reducer exists because more than one node feeds SPEAKING."""
    state.update(narration=queue(state["narration"], introducing(state, deps)["narration"]))
    state.update(narration=queue(state["narration"], planning(state, deps)["narration"]))
    assert len(state["narration"]) == 2
    speaking(state, deps)
    assert deps.speaker.said[0] == render_intro(
        deps.graph.effective_persona()
    ), "the intro must actually be spoken"


# --- routing -----------------------------------------------------------------


def test_routes_back_to_executing_while_calls_remain():
    assert after_speaking({"pending_calls": [object()]}) == "executing"


def test_routes_to_listening_after_the_intro():
    """No plan yet means SPEAKING was reached from INTRODUCING."""
    assert after_speaking({"pending_calls": [], "plan": None}) == "listening"


def test_routes_to_reflecting_only_on_failure():
    done = {"pending_calls": [], "plan": object()}
    assert after_speaking({**done, "failures": [object()]}) == "reflecting"
    assert after_speaking({**done, "failures": []}) == "turn_done"


def test_turn_routing_respects_max_turns():
    assert after_turn({"turns": 1, "max_turns": 2}) == "listening"
    assert after_turn({"turns": 1, "max_turns": 1}) == "ending"


def test_turn_routing_ends_when_phase_ending():
    assert after_turn({"turns": 0, "max_turns": 50, "phase": "ending"}) == "ending"
    assert after_turn({"turns": 0, "max_turns": 50, "finished": True}) == "ending"


# --- the whole loop ----------------------------------------------------------


def test_scripted_demo_runs_end_to_end(deps, tmp_path):
    state = initial_state(uuid4(), "inbox")
    final = build_graph(deps).invoke(state)

    assert final["finished"]
    assert final["failures"] == []

    entries = deps.log.entries(state["session_id"])
    assert [e.tool_call.tool for e in entries] == [
        "navigate",
        "wait_for",
        "fill_field",
        "click_element",
    ]
    assert all(e.verify.passed for e in entries)

    # The message really landed in the DOM, not just in the log.
    assert deps.page.inner_text(".message.sent") == "Hi from Navigator AI"

    # And it was narrated out loud, step by step.
    assert len(deps.speaker.said) >= 5

    # Archived under the product's own directory, not a shared one.
    archived = list((tmp_path / "archives" / deps.product_id).glob("*"))
    assert len(archived) == 2


def test_broken_site_graph_records_a_failure_and_keeps_going(deps, tmp_path):
    """The plan's last step points at a selector that no longer matches."""
    broken = deps.graph.model_copy(deep=True)
    object.__setattr__(
        broken.pages["inbox"], "selectors",
        {**broken.pages["inbox"].selectors, "send_button": "#no-such-button"},
    )
    deps.graph = broken

    state = initial_state(uuid4(), "inbox")
    final = build_graph(deps).invoke(state)

    assert final["finished"], "a failure must not abort the call"
    assert len(final["failures"]) == 1
    (failure,) = deps.log.failures(state["session_id"])
    assert failure.tool_call.tool == "click_element"
    assert not failure.actual_result.ok
    assert failure.expected_postcondition.selector == "sent_bubble"
