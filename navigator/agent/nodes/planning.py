"""PLANNING: decide what to do and what to say.

Scripted path (CallDeps.scripted_flow set): replay a named flow — deterministic,
used by demo/CI. LLM path: retrieve memory, pick a flow_id via Groq or an
injectable chooser, expand tool_calls from the site graph. The model never
invents selectors or postconditions.
"""

from __future__ import annotations

from navigator.agent.planner import FlowChoice, choose_flow
from navigator.agent.state import CallDeps, CallState
from navigator.memory.retrieval import retrieve_corrections, retrieve_product_knowledge
from navigator.schemas import Plan
from navigator.settings import settings


def planning(state: CallState, deps: CallDeps) -> CallState:
    if deps.scripted_flow is not None:
        page_id, flow_id = deps.scripted_flow
        return _plan_from_flow(
            deps,
            page_id,
            flow_id,
            spoken=_describe(deps.graph.page(page_id).name, flow_id),
        )

    page_id = state.get("page_id") or ""
    page = deps.graph.page(page_id)
    flow_ids = sorted(page.flows)
    if not flow_ids:
        raise RuntimeError(f"page {page_id!r} has no flows to choose from")

    chroma_path = (
        deps.chroma_path if deps.chroma_path is not None else settings.chroma_path
    )
    transcript = list(state.get("transcript") or [])
    query = _query_from_transcript(transcript)

    corrections = retrieve_corrections(
        deps.product_id,
        query,
        page=page_id,
        tool_call_type=None,
        path=chroma_path,
    )
    knowledge = retrieve_product_knowledge(
        deps.product_id, query, path=chroma_path
    )
    persona = deps.graph.effective_persona()

    chooser_kwargs = dict(
        page_id=page_id,
        flow_ids=flow_ids,
        transcript=transcript,
        corrections=corrections,
        knowledge=knowledge,
        persona=persona,
    )

    if deps.choose_flow is not None:
        choice = deps.choose_flow(**chooser_kwargs)
    else:
        api_key = (
            deps.groq_api_key
            if deps.groq_api_key is not None
            else settings.groq_api_key
        )
        if not api_key:
            raise RuntimeError(
                "PLANNING needs CallDeps.scripted_flow=(page_id, flow_id) "
                "or a Groq API key (CallDeps.groq_api_key / NAVIGATOR_GROQ_API_KEY)"
            )
        choice = choose_flow(api_key=api_key, **chooser_kwargs)

    if not isinstance(choice, FlowChoice):
        choice = FlowChoice.model_validate(choice)

    if choice.flow_id not in page.flows:
        raise ValueError(f"flow_id {choice.flow_id!r} not in allowed {flow_ids}")

    return _plan_from_flow(
        deps, page_id, choice.flow_id, spoken=choice.spoken_response
    )


def _plan_from_flow(
    deps: CallDeps, page_id: str, flow_id: str, *, spoken: str
) -> CallState:
    calls = deps.graph.flow(page_id, flow_id)
    plan = Plan(spoken_response=spoken, tool_calls=list(calls))
    return CallState(
        plan=plan,
        pending_calls=list(plan.tool_calls),
        narration=[plan.spoken_response],
        transcript=[f"agent: {plan.spoken_response}"],
    )


def _query_from_transcript(transcript: list[str]) -> str:
    for line in reversed(transcript):
        if line.startswith("user:"):
            return line.removeprefix("user:").strip()
    return " ".join(transcript[-5:]) if transcript else ""


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
