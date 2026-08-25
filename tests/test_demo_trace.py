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


def test_action_starts_while_prior_speech_still_playing() -> None:
    """Hands must not wait for mouth — navigate while prior line is still speaking."""
    import threading

    from navigator.meeting.playback_handle import PlaybackHandle

    events: list[tuple[str, float]] = []
    prior = PlaybackHandle()

    def _prior_hold() -> None:
        time.sleep(0.35)
        prior._finish()

    threading.Thread(target=_prior_hold, name="prior-speech", daemon=True).start()

    deps = _deps(events)
    call = ClickElement(
        tool="click_element",
        selector="go",
        expects=Postcondition(check="visible", selector="go"),
    )
    state = {
        "narration": ["Opening next"],
        "pending_calls": [call],
        "page_id": "home",
        "pre_action_speech": prior,
    }

    def run_tool(*_args, **_kwargs):
        events.append(("action", time.monotonic()))
        return ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1), "home"

    t0 = time.monotonic()
    with patch("navigator.agent.nodes.executing.run_tool", side_effect=run_tool):
        executing(state, deps)
    action_at = next(t for k, t in events if k == "action")
    assert action_at - t0 < 0.2, "must not block Playwright on prior speech"


def test_speaking_with_pending_calls_never_hits_live_say() -> None:
    class FakeLive:
        def __init__(self) -> None:
            self.said: list[tuple[str, str]] = []

        def say(self, text: str, *, mode: str = "verbatim", utterance_id: str | None = None) -> None:
            self.said.append((text, mode))

    events: list[tuple[str, float]] = []
    deps = _deps(events)
    live = FakeLive()
    deps.live_agent = live
    call = ClickElement(
        tool="click_element",
        selector="go",
        expects=Postcondition(check="visible", selector="go"),
    )
    state = {"narration": ["Going to Send Campaign"], "pending_calls": [call]}
    speaking(state, deps)
    assert live.said == []


def test_same_step_action_overlaps_slow_narration() -> None:
    """run_tool must start before a slow say finishes (same step)."""
    events: list[tuple[str, float]] = []

    class SlowSpeaker:
        def say(self, _text: str) -> None:
            events.append(("speech_start", time.monotonic()))
            time.sleep(0.3)
            events.append(("speech_end", time.monotonic()))

    graph = MagicMock()
    graph.pages = {}
    graph.base_url = "https://app.test/"
    page = MagicMock()
    page.url = "https://app.test/"
    deps = CallDeps(
        graph=graph,
        page=page,
        log=MagicMock(),
        speaker=SlowSpeaker(),
        product_id="acme",
    )
    call = ClickElement(
        tool="click_element",
        selector="go",
        expects=Postcondition(check="visible", selector="go"),
    )
    state = {"narration": ["Opening Go"], "pending_calls": [call], "page_id": "home"}

    def run_tool(*_args, **_kwargs):
        events.append(("action", time.monotonic()))
        return ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1), "home"

    with patch("navigator.agent.nodes.executing.run_tool", side_effect=run_tool):
        executing(state, deps)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not any(k == "speech_end" for k, _ in events):
        time.sleep(0.02)
    by_kind = {k: t for k, t in events}
    assert "speech_start" in by_kind and "action" in by_kind and "speech_end" in by_kind
    assert by_kind["action"] < by_kind["speech_end"]
    assert by_kind["action"] - by_kind["speech_start"] < 0.15


def test_live_say_async_returns_before_mouth_finishes() -> None:
    """Live say_async must not block the demo thread on live.say."""
    import time as _time

    from navigator.meeting.live_demo import _own_meet_tts_when_live

    class SlowLive:
        def __init__(self) -> None:
            self.started = False
            self.done = False

        def say(self, text: str, mode: str = "natural", **_k) -> None:
            self.started = True
            _time.sleep(0.35)
            self.done = True

    class Meet:
        def say(self, text: str, **_k) -> None:
            pass

        def say_async(self, text: str):
            raise AssertionError("orig say_async should be replaced")

    live = SlowLive()
    meet = Meet()
    _own_meet_tts_when_live(meet, [live])
    t0 = _time.monotonic()
    handle = meet.say_async("hello while navigating")
    assert _time.monotonic() - t0 < 0.1
    assert not live.done
    handle.wait(timeout=2.0)
    assert live.done
