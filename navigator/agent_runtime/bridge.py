"""Wire AgentOrchestrator into live demo + LangGraph CallDeps."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from navigator.agent_runtime.models import AgentSession
from navigator.agent_runtime.orchestrator import AgentOrchestrator
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


def on_live_heard(deps: Any, text: str) -> bool:
    """Returns True if orchestrator consumed the utterance (complex task)."""
    orch = getattr(deps, "orchestrator", None)
    if orch is None:
        return False
    decision = orch.handle_utterance(text)
    return decision.route == "orchestrator"
