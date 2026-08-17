"""The call state machine.

An explicit graph, not an agent loop. The interesting property is the
EXECUTING -> VERIFYING -> SPEAKING cycle: it repeats once per tool call, so the
agent never builds on top of an action it hasn't verified.

    joining -> introducing ------+
                                 v
              listening -> planning -> speaking <-- verifying <-- executing
                  ^                       |                          ^
                  |          +------------+-------------+------------+
                  |          |            |             |     pending calls
                  |     no plan yet   failures      all done
                  |          |            |             |
                  +----------+            v             v
                                     reflecting --> turn_done
                                                        |
                                                   turns left?
                                                     /      \
                                                    v        v
                                              listening    ending

Everything that talks routes through SPEAKING, so one node owns all TTS and
narration can't be spoken twice or dropped. `after_speaking` decides where to go
based on why SPEAKING was reached.

Every node is a plain function of (CallState, CallDeps) returning a partial state,
so each is testable on its own with a dict and a fake CallDeps -- no graph, no
browser, no LangGraph.
"""

from __future__ import annotations

from functools import partial
from typing import Literal
from uuid import UUID

from langgraph.graph import END, StateGraph

from navigator.agent.nodes.ending import ending
from navigator.agent.nodes.executing import executing
from navigator.agent.nodes.introducing import introducing
from navigator.agent.nodes.joining import joining
from navigator.agent.nodes.listening import listening
from navigator.agent.nodes.planning import planning
from navigator.agent.nodes.reflecting import reflecting
from navigator.agent.nodes.speaking import speaking
from navigator.agent.nodes.verifying import verifying
from navigator.agent.state import CallDeps, CallState, initial_state

NODES = (
    ("joining", joining),
    ("introducing", introducing),
    ("listening", listening),
    ("planning", planning),
    ("executing", executing),
    ("verifying", verifying),
    ("speaking", speaking),
    ("reflecting", reflecting),
    ("ending", ending),
)


def after_speaking(
    state: CallState,
) -> Literal["listening", "executing", "reflecting", "turn_done", "ending"]:
    """SPEAKING is the single TTS owner, so several paths converge on it.

    Where it goes next depends on why it was reached: the intro (no plan yet),
    mid-plan (calls still pending), or end of turn.
    """
    if state.get("finished") or state.get("phase") == "ending":
        return "ending"
    if state.get("pending_calls"):
        return "executing"
    if state.get("plan") is None:
        return "listening"  # the intro was just spoken
    if state.get("failures"):
        # Only failures earn a reflection pass; a clean turn skips the LLM entirely.
        return "reflecting"
    return "turn_done"


def after_turn(state: CallState) -> Literal["listening", "ending"]:
    if state.get("phase") == "ending" or state.get("finished"):
        return "ending"
    return "listening" if _turns_left(state) else "ending"


def _turns_left(state: CallState) -> bool:
    return state.get("turns", 0) < state.get("max_turns", 1)


def turn_done(state: CallState, deps: CallDeps) -> CallState:
    """Bookkeeping: one completed listen -> speak cycle."""
    return CallState(turns=state.get("turns", 0) + 1)


def anything_else_entry_state(
    session_id: UUID,
    page_id: str,
    *,
    max_turns: int,
    walkthrough_flow_id: str = "",
) -> CallState:
    """State to enter SPEAKING with the post-demo Q&A prompt already queued."""
    from navigator.agent.end_policy import ANYTHING_ELSE
    from navigator.core.schemas import Plan

    state = initial_state(
        session_id,
        page_id,
        max_turns=max_turns,
        walkthrough_flow_id=walkthrough_flow_id,
        auto_play=False,
    )
    state["phase"] = "anything_else"
    state["plan"] = Plan(spoken_response=ANYTHING_ELSE, tool_calls=[])
    state["pending_calls"] = []
    state["narration"] = [ANYTHING_ELSE]
    return state


def build_graph(deps: CallDeps, *, entry: str = "joining"):
    """Wire and compile the graph. `deps` is bound into every node."""
    builder = StateGraph(CallState)

    for name, fn in NODES:
        builder.add_node(name, partial(fn, deps=deps))
    builder.add_node("turn_done", partial(turn_done, deps=deps))

    allowed = {name for name, _ in NODES} | {"turn_done"}
    if entry not in allowed:
        raise ValueError(f"unknown graph entry {entry!r}")
    builder.set_entry_point(entry)
    builder.add_edge("joining", "introducing")
    builder.add_edge("introducing", "speaking")
    builder.add_edge("listening", "planning")
    builder.add_edge("planning", "speaking")
    builder.add_edge("executing", "verifying")
    builder.add_edge("verifying", "speaking")
    builder.add_edge("reflecting", "turn_done")

    builder.add_conditional_edges("speaking", after_speaking)
    builder.add_conditional_edges("turn_done", after_turn)
    builder.add_edge("ending", END)

    # A scripted turn is ~4 calls x 3 nodes; the cap only guards against a
    # routing bug looping forever.
    return builder.compile().with_config(recursion_limit=100)
