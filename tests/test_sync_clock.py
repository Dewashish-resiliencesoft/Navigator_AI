"""Narration chunks and browser actions share one monotonic clock."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from navigator.agent_runtime.events.bus import EventBus
from navigator.agent_runtime.models import AgentEventKind
from navigator.agent_runtime.orchestrator import AgentOrchestrator
from navigator.agent_runtime.sync_clock import (
    emit_browser_action,
    emit_narration_chunk,
    sync_events,
)
from navigator.automation.browser import tools
from navigator.core.schemas import Postcondition, WaitFor
from navigator.voice.live_agent import LiveAgent, LiveAgentConfig


def _since(start: int):
    return sync_events.history()[start:]


def test_narration_and_browser_share_an_increasing_clock():
    start = len(sync_events.history())
    spoken = emit_narration_chunk("Hello there.", "gemini_live")
    acted = emit_browser_action("click_element", "start")
    assert spoken.event == AgentEventKind.NARRATION_CHUNK
    assert acted.event == AgentEventKind.BROWSER_ACTION
    assert spoken.monotonic_ns > 0
    assert acted.monotonic_ns >= spoken.monotonic_ns
    assert spoken.payload["monotonic_ns"] == spoken.monotonic_ns
    assert acted.payload["monotonic_ns"] == acted.monotonic_ns
    assert spoken.payload["provider"] == "gemini_live"
    assert acted.payload == {
        "tool": "click_element",
        "phase": "start",
        "monotonic_ns": acted.monotonic_ns,
    }
    kinds = [event.event for event in _since(start)]
    assert kinds[-2:] == [AgentEventKind.NARRATION_CHUNK, AgentEventKind.BROWSER_ACTION]


def test_first_audio_emits_one_narration_chunk():
    agent = LiveAgent(LiveAgentConfig(api_key="x", system_instruction="s"), object())
    start = len(sync_events.history())
    try:
        agent._arm_narration_chunk("Pricing is below.", provider="nvidia")
        agent._in_say = True
        agent._say_queued_at = 0.0
        agent._note_first_audio()
        agent._note_first_audio()
    finally:
        agent._loop.close()
    chunks = [
        event
        for event in _since(start)
        if event.event == AgentEventKind.NARRATION_CHUNK
    ]
    assert len(chunks) == 1
    assert chunks[0].payload["text"] == "Pricing is below."
    assert chunks[0].payload["provider"] == "nvidia"
    assert chunks[0].monotonic_ns > 0


def test_execute_stamps_action_start_and_end(monkeypatch):
    def fake_handler(page, graph, page_id, call, *, on_frame=None, mouse_path=None):
        return "clicked pricing", page_id

    monkeypatch.setitem(tools._HANDLERS, "wait_for", fake_handler)
    call = WaitFor(
        expects=Postcondition(check="url_matches", expected="https://example.test"),
        selector="ready",
        timeout_ms=50,
    )
    start = len(sync_events.history())
    result, page_id = tools.execute(object(), object(), "home", call)
    assert result.ok is True
    assert page_id == "home"
    actions = [
        event
        for event in _since(start)
        if event.event == AgentEventKind.BROWSER_ACTION
    ]
    assert [event.payload["phase"] for event in actions] == ["start", "end"]
    assert actions[0].payload["tool"] == "wait_for"
    assert actions[1].payload["ok"] is True
    assert actions[1].monotonic_ns >= actions[0].monotonic_ns
    assert actions[0].payload["monotonic_ns"] == actions[0].monotonic_ns


def test_orchestrator_events_land_on_the_shared_clock():
    session_id = uuid4()
    bus = EventBus()
    fake = SimpleNamespace(
        store=SimpleNamespace(
            state=SimpleNamespace(
                session=SimpleNamespace(session_id=session_id),
                execution=SimpleNamespace(flow_id="flow", step_id="step"),
            ),
            version=lambda: 4,
        ),
        events=bus,
    )
    start = len(sync_events.history())
    AgentOrchestrator._emit(
        fake,
        AgentEventKind.ACTION_STARTED,
        payload={"tool": "click_element"},
    )
    own = bus.history()[-1]
    assert own.monotonic_ns > 0
    assert own.payload["monotonic_ns"] == own.monotonic_ns
    copied = [
        event
        for event in _since(start)
        if event.event == AgentEventKind.ACTION_STARTED and event.session_id == session_id
    ]
    assert len(copied) == 1
    assert copied[0].monotonic_ns == own.monotonic_ns
    assert copied[0].payload["monotonic_ns"] == own.monotonic_ns
