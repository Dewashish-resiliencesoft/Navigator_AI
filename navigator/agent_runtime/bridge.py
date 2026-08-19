"""Wire AgentOrchestrator into live demo + LangGraph CallDeps.

on_live_heard() is the seam between Gemini Live and the orchestrator.
It applies the 3-way router (BACKCHANNEL / ANSWER / TASK_HANDOFF),
emits an immediate natural ack for complex tasks, and delegates async.

Groq is NOT on this synchronous path.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from navigator.agent_runtime.models import AgentSession
from navigator.agent_runtime.orchestrator import AgentOrchestrator
from navigator.agent_runtime.planning.router import RouteDecision, classify_utterance
from navigator.agent_runtime.realtime_state import RealtimeController, RealtimeState
from navigator.agent_runtime.backchannel import BackchannelController
from navigator.core.settings import settings


def build_orchestrator(
    *,
    session_id: UUID,
    product_id: str,
    revision_id: int,
    origin: str,
    deps: Any,
) -> AgentOrchestrator | None:
    if not settings.agent_runtime_enabled:
        return None
    session = AgentSession(
        session_id=session_id,
        product_id=product_id,
        revision_id=revision_id,
        origin=origin,  # type: ignore[arg-type]
    )
    orch = AgentOrchestrator(
        session=session,
        graph=deps.graph,
        page=deps.page,
        log=deps.log,
        page_id=getattr(deps, "page_id", None) or next(iter(deps.graph.pages), ""),
        live_agent=getattr(deps, "live_agent", None),
        brain_config=getattr(deps, "brain_config", None),
        on_frame=getattr(deps, "on_frame", None),
        speak=lambda t: deps.speaker.say(t) if getattr(deps, "speaker", None) else None,
    )
    orch.refresh_browser_state()
    return orch


def attach_to_deps(deps: Any, orchestrator: AgentOrchestrator | None) -> None:
    deps.orchestrator = orchestrator  # type: ignore[attr-defined]


def on_live_heard(
    deps: Any,
    text: str,
    *,
    rt_controller: RealtimeController | None = None,
    backchannel_ctl: BackchannelController | None = None,
) -> RouteDecision:
    """Route one user utterance through the 3-way classifier.

    Returns the RouteDecision so the caller knows what happened.
    Side effects:
      - BACKCHANNEL: rate-limited nudge via backchannel_ctl (optional)
      - ANSWER: Live handles it; no orchestrator call
      - TASK_HANDOFF: immediate ack + async delegation to orchestrator
    """
    is_working = rt_controller.is_working if rt_controller else False
    decision = classify_utterance(text, agent_working=is_working)

    orch = getattr(deps, "orchestrator", None)

    if decision.route == "backchannel":
        if backchannel_ctl is not None:
            backchannel_ctl.maybe_backchannel(context_hint=text[:80])
        if rt_controller:
            rt_controller.transition(RealtimeState.BACKCHANNELING)
        return decision

    if decision.route == "answer":
        if rt_controller:
            rt_controller.transition(RealtimeState.RESPONDING)
        # Live answers from its own context — no orchestrator needed
        return decision

    # TASK_HANDOFF
    if rt_controller:
        rt_controller.transition(RealtimeState.DELEGATING, goal=text)

    # Emit immediate natural ack (Live generates wording from hint)
    live = getattr(deps, "live_agent", None)
    if live is not None:
        from navigator.agent_runtime.adapters.live_adapter import LiveAdapter
        adapter = LiveAdapter(live)
        adapter.acknowledge(decision.ack_hint or text)

    if orch is not None:
        if rt_controller:
            rt_controller.transition(RealtimeState.WORKING, goal=text)
        orch_decision = orch.handle_utterance(text)
        _ = orch_decision  # orchestrator runs the task

    return decision
