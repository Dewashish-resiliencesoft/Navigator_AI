"""Unit tests for agent runtime contracts and routing."""

from __future__ import annotations

from uuid import uuid4

from navigator.agent_runtime.dom.builder import semantic_id_for
from navigator.agent_runtime.models import AgentSession, AgentWorldState
from navigator.agent_runtime.planning.router import classify_utterance


def test_semantic_id_slug():
    el = {"tag": "button", "text": "Analytics"}
    assert semantic_id_for(el).startswith("button")


def test_router_simple_ack():
    d = classify_utterance("okay")
    assert d.route == "live_direct"


def test_router_complex_browser_task():
    d = classify_utterance("Open analytics and compare March with April")
    assert d.route == "orchestrator"
    assert d.reason == "browser_task"


def test_world_state_version_increments():
    from navigator.agent_runtime.world_state.store import WorldStateStore

    session = AgentSession(session_id=uuid4(), product_id="demo")
    store = WorldStateStore(AgentWorldState(session=session))
    assert store.version() == 0
    store.update(lambda s: s.model_copy(update={"conversation": s.conversation.model_copy(update={"last_user_message": "hi"})}))
    assert store.version() == 1
    assert store.state.conversation.last_user_message == "hi"
