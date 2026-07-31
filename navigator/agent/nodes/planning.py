"""PLANNING: decide what to do and what to say.

Phase 1 replays a named flow straight out of the site graph -- no LLM, fully
deterministic, which is what makes the rest of the loop testable. The Plan shape
it returns is exactly what the Phase 2 LLM will be constrained to emit, so nothing
downstream changes when the real planner lands.
"""

from __future__ import annotations

from navigator.agent.state import CallDeps, CallState
from navigator.schemas import Plan


def planning(state: CallState, deps: CallDeps) -> CallState:
    # TODO(phase 2): replace the branch below with a Groq llama-3.3-70b-versatile
    # call taking (recent transcript, product knowledge from Chroma, corrections
    # retrieved for state["page_id"] and the likely tool_call_type, the site graph)
    # and returning a Plan validated against the Pydantic schema. Free tier is
    # 30 RPM / 1000 RPD, which is one plan per conversational turn.
    if deps.scripted_flow is None:
        raise RuntimeError(
            "Phase 1 PLANNING needs CallDeps.scripted_flow=(page_id, flow_id)"
        )

    page_id, flow_id = deps.scripted_flow
    calls = deps.graph.flow(page_id, flow_id)
    plan = Plan(
        spoken_response=_describe(deps.graph.page(page_id).name, flow_id),
        tool_calls=list(calls),
    )
    return CallState(
        plan=plan,
        pending_calls=list(plan.tool_calls),
        narration=[plan.spoken_response],
        transcript=[f"agent: {plan.spoken_response}"],
    )


def _describe(page_name: str, flow_id: str) -> str:
    """Announce a flow using the customer's own naming.

    Reads flow ids as English the same way narration reads selector aliases, so a
    well-named site graph produces sensible speech on any product with no code
    change here.
    """
    flow_name = flow_id.replace("_", " ").replace("-", " ")
    return (
        f"Sure, let me show you. I'll walk through {flow_name} on the "
        f"{page_name} page, step by step."
    )
