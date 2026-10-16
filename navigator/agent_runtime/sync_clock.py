"""Shared monotonic clock for narration and browser actions.

LangGraph live demos do not pass through the orchestrator bus. This
process-wide bus carries the same ``time.monotonic_ns()`` stamp on each
narration chunk and each browser action so a future video-capture
pipeline can align them. Orchestrator events are copied here as well.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID, uuid4

from navigator.agent_runtime.events.bus import EventBus
from navigator.agent_runtime.models import AgentEvent, AgentEventKind

#: One bus for the process. A video pipeline subscribes here.
sync_events = EventBus()

#: Used when the LangGraph path has no orchestrator session.
FALLBACK_SESSION_ID = uuid4()


def emit_narration_chunk(
    text: str,
    provider: str,
    *,
    session_id: UUID | None = None,
) -> AgentEvent:
    """Stamp the first audible piece of one narration sentence."""
    return _emit(
        AgentEventKind.NARRATION_CHUNK,
        session_id=session_id,
        source="voice",
        payload={"text": text, "provider": provider},
    )


def emit_browser_action(
    tool: str,
    phase: str,
    *,
    ok: bool | None = None,
    detail: str = "",
    session_id: UUID | None = None,
) -> AgentEvent:
    """Stamp the start or end of one Playwright tool call."""
    payload: dict[str, Any] = {"tool": tool, "phase": phase}
    if ok is not None:
        payload["ok"] = ok
    if detail:
        payload["detail"] = detail[:240]
    return _emit(
        AgentEventKind.BROWSER_ACTION,
        session_id=session_id,
        source="browser",
        payload=payload,
    )


def forward_event(event: AgentEvent) -> None:
    """Copy an orchestrator event onto the shared clock bus."""
    stamp = int(event.monotonic_ns or time.monotonic_ns())
    payload = dict(event.payload or {})
    payload.setdefault("monotonic_ns", stamp)
    stamped = event.model_copy(update={"monotonic_ns": stamp, "payload": payload})
    sync_events.emit(stamped)


def _emit(
    kind: AgentEventKind,
    *,
    session_id: UUID | None,
    source: str,
    payload: dict[str, Any],
) -> AgentEvent:
    stamp = time.monotonic_ns()
    body = dict(payload)
    body["monotonic_ns"] = stamp
    event = AgentEvent(
        event=kind,
        session_id=session_id or FALLBACK_SESSION_ID,
        source=source,
        monotonic_ns=stamp,
        payload=body,
    )
    sync_events.emit(event)
    return event
