"""Wire AgentOrchestrator into live demo + LangGraph CallDeps.

There is ONE canonical path for a heard user utterance:

    LiveAgent transcript
        ↓
    on_live_heard()
        ↓
    orchestrator.handle_utterance()  ← routing + state update + dispatch
        ↓
    BACKCHANNEL / ANSWER / TASK_HANDOFF

Do NOT duplicate routing logic in live_demo.py or LiveAgent.
Do NOT feed bot/echo back through this path — callers must filter first.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from navigator.agent_runtime.models import AgentSession
from navigator.agent_runtime.orchestrator import AgentOrchestrator
from navigator.agent_runtime.planning.router import (
    ROUTE_BACKCHANNEL,
    ROUTE_ANSWER,
    ROUTE_TASK_HANDOFF,
    RouteDecision,
)
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


def on_live_heard(deps: Any, text: str) -> RouteDecision | None:
    """Canonical entry point for a heard user utterance.

    Returns the RouteDecision so callers can introspect the route if needed.
    Returns None when the orchestrator is not enabled.

    Route semantics:
    ─────────────────
    BACKCHANNEL   — Live may optionally emit a brief natural ack or stay silent.
    ANSWER        — Live answers directly; no orchestrator involvement.
    TASK_HANDOFF  — immediate ack already sent; orchestrator executes async.
    """
    orch: AgentOrchestrator | None = getattr(deps, "orchestrator", None)
    if orch is None:
        return None

    decision = orch.handle_utterance(text)
    return decision
