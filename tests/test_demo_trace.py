from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from navigator.agent.nodes.executing import executing
from navigator.agent.nodes.speaking import speaking
from navigator.agent.state import CallDeps
from navigator.core.schemas import ClickElement, Postcondition, ToolResult


def _deps(events: list[tuple[str, float]]) -> CallDeps:
    class Speaker:
        def say(self, _text: str) -> None:
            events.append(("narration", time.monotonic()))

    graph = MagicMock()
    graph.pages = {}
    graph.base_url = "https://app.test/"
    page = MagicMock()
    page.url = "https://app.test/"
    return CallDeps(
        graph=graph,
        page=page,
        log=MagicMock(),
        speaker=Speaker(),
        product_id="acme",
    )


def test_langgraph_queues_pre_action_narration_until_execution() -> None:
    events: list[tuple[str, float]] = []
    deps = _deps(events)
    trace_events: list[dict] = []
    deps.trace = trace_events.append
    call = ClickElement(
        tool="click_element",
        selector="go",
        expects=Postcondition(check="visible", selector="go"),
    )
    state = {"narration": ["Opening Go"], "pending_calls": [call], "page_id": "home"}

    speaking(state, deps)
    assert events == []

    def run_tool(*_args, **_kwargs):
        events.append(("action", time.monotonic()))
        return ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1), "home"

    with patch("navigator.agent.nodes.executing.run_tool", side_effect=run_tool):
        executing(state, deps)

    assert [kind for kind, _ in events] == ["narration", "action"]
    assert 0 <= (events[1][1] - events[0][1]) < 0.15
    sync = [event for event in trace_events if event["event"] == "narration_action_sync"]
    assert sync and 0 <= sync[0]["gap_ms"] < 150


def test_engine_selection_reports_timeline_branch() -> None:
    from navigator.meeting.live_demo import select_engine

    assert select_engine(
        live_agent_present=False,
        playlist_demo=True,
        timeline_ready=True,
        conversational=False,
    ) == ("timeline", "playlist metadata complete")


def test_gemini_live_turn_uses_shared_low_gap_execution_trace() -> None:
    events: list[tuple[str, float]] = []
    deps = _deps(events)
    deps.live_agent = MagicMock()
    trace_events: list[dict] = []
    deps.trace = trace_events.append
    call = ClickElement(
        tool="click_element",
        selector="go",
        expects=Postcondition(check="visible", selector="go"),
    )
    state = {
        "narration": ["Opening Go"],
        "pending_calls": [call],
        "page_id": "home",
        "walkthrough_flow_id": "demo",
    }

    speaking(state, deps)
    with patch(
        "navigator.agent.nodes.executing.run_tool",
        return_value=(
            ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1),
            "home",
        ),
    ):
        executing(state, deps)

    sync = [event for event in trace_events if event["event"] == "narration_action_sync"]
    assert sync and sync[0]["engine"] == "gemini_live"
    assert 0 <= sync[0]["gap_ms"] < 150
