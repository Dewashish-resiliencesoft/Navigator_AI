"""Recorded flow scrub, step_clicks, timeline playback, strict playlist."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import yaml

from navigator.agent.nodes.planning import planning
from navigator.agent.nodes.verifying import verifying
from navigator.agent.recorded_playback import run_flow_timeline
from navigator.agent.state import CallDeps, initial_state
from navigator.automation.record import RecordedStep
from navigator.automation.record_scrub import (
    scrub_recorded_steps,
    step_clicks_payload,
)
from navigator.client.content import merge_recorded_flow
from navigator.core.schemas import (
    ClickElement,
    FillField,
    Postcondition,
    ToolResult,
    VerifyResult,
)
from navigator.knowledge.site_graph import DemoPlaylistItem, PageSpec, SiteGraph, parse_site_graph


def _click(alias: str) -> ClickElement:
    return ClickElement(
        tool="click_element",
        selector=alias,
        expects=Postcondition(check="visible", selector=alias, timeout_ms=1000),
    )


def _minimal_yaml() -> str:
    return yaml.safe_dump(
        {
            "version": 1,
            "site": "acme",
            "base_url": "https://app.acme.test/",
            "pages": {
                "home": {
                    "name": "Home",
                    "url": "/",
                    "selectors": {"signup": "text=Sign up", "dark_mode": ".theme"},
                    "flows": {},
                }
            },
            "demo_playlist": [],
        },
        sort_keys=False,
    )


def test_scrub_recorded_steps_drops_theme_toggle():
    steps = [
        RecordedStep(
            tool="click_element",
            alias="dark_mode",
            selector="dark_mode",
            at_ms=100,
        ),
        RecordedStep(
            tool="click_element",
            alias="signup",
            selector="signup",
            at_ms=5000,
        ),
    ]
    cleaned = scrub_recorded_steps(steps)
    assert len(cleaned) == 1
    assert cleaned[0].alias == "signup"


def test_merge_recorded_flow_scrubs_junk_before_save():
    steps = [
        RecordedStep(
            tool="click_element",
            alias="dark_mode",
            selector="dark_mode",
            at_ms=100,
        ),
        RecordedStep(
            tool="click_element",
            alias="signup",
            selector="signup",
            at_ms=5000,
        ),
    ]
    merged = merge_recorded_flow(
        _minimal_yaml(),
        flow_name="Auth",
        flow_id="authentication_flow",
        page_id="home",
        steps=steps,
        product_name="Acme",
        base_url="https://app.acme.test/",
    )
    graph = parse_site_graph(merged)
    calls = list(graph.flow("home", "authentication_flow"))
    assert len(calls) == 1
    assert calls[0].selector == "signup"


def test_step_clicks_payload_and_reader():
    steps = [
        RecordedStep(
            tool="click_element",
            alias="a",
            selector="a",
            at_ms=879,
        ),
        RecordedStep(
            tool="click_element",
            alias="b",
            selector="b",
            at_ms=20001,
        ),
    ]
    payload = step_clicks_payload(steps)
    assert payload == [{"idx": 0, "at_ms": 879}, {"idx": 1, "at_ms": 20001}]

    raw = yaml.safe_dump(
        {
            "version": 1,
            "site": "acme",
            "base_url": "https://app.acme.test/",
            "pages": {"home": {"name": "H", "url": "/", "selectors": {}, "flows": {}}},
            "_meta": {"step_clicks": {"demo": payload}},
        },
        sort_keys=False,
    )
    graph = parse_site_graph(raw)
    assert graph.flow_step_clicks("demo") == {0: 879, 1: 20001}
    assert graph.has_recorded_playback("demo") is False  # no narration


def test_has_recorded_playback_with_clicks_and_placeholders():
    raw = {
        "version": 1,
        "site": "acme",
        "base_url": "https://app.acme.test/",
        "pages": {"home": {"name": "H", "url": "/", "selectors": {}, "flows": {}}},
        "_meta": {
            "narration_suggestions": {"demo": ["Here is signup."]},
            "step_clicks": {"demo": [{"idx": 0, "at_ms": 879}]},
        },
    }
    import yaml

    graph = parse_site_graph(yaml.safe_dump(raw, sort_keys=False))
    assert graph.has_recorded_playback("demo") is True


def test_execute_call_unpacks_run_tool_tuple():
    from navigator.agent.recorded_playback import _execute_call

    graph = SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={"signup": "text=Sign up"},
                flows={"demo": (_click("signup"),)},
            ),
        },
    )
    ok = ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1)
    frames: list[str] = []

    def push_frame() -> None:
        frames.append("frame")

    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=MagicMock(),
        product_id="acme",
        push_frame=push_frame,
    )
    with patch(
        "navigator.agent.recorded_playback.run_tool",
        return_value=(ok, "home"),
    ) as run_tool_mock:
        _call, result, page_id = _execute_call(
            deps, _click("signup"), page_id="home"
        )
    assert result.ok is True
    assert page_id == "home"
    run_tool_mock.assert_called_once()
    assert run_tool_mock.call_args.kwargs.get("on_frame") is push_frame
    assert frames == ["frame", "frame"]


def test_recorded_gaps_are_not_clamped():
    """Long recorded gaps used to collapse to 1.2s. They now replay verbatim."""
    from navigator.agent.playback_schedule import build_schedule

    cues, _total = build_schedule(
        n_steps=3,
        clicks={0: 0, 1: 12697, 2: 34809},
        speech={},
        lines=["a", "b", "c"],
        timing={},
        tts_ms=lambda _t: None,
    )
    acts = [c.at_ms for c in cues if c.kind == "act"]
    assert acts == [0, 12697, 34809]


def test_timeline_parallel_speech_and_click():
    events: list[tuple[str, float]] = []

    class AsyncSpeaker:
        def say_async(self, text: str):
            events.append(("speak", time.monotonic()))
            handle = MagicMock()
            handle.wait = MagicMock()
            handle.cancel = MagicMock()
            return handle

        def say(self, text: str) -> None:
            events.append(("say", time.monotonic()))

    graph = SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={"signup": "text=Sign up"},
                flows={"demo": (_click("signup"),)},
            ),
        },
        meta={
            "narration_suggestions": {"demo": ["Opening signup now"]},
            "step_clicks": {"demo": [{"idx": 0, "at_ms": 800}]},
        },
    )
    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=AsyncSpeaker(),
        product_id="acme",
    )
    ok_result = ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1)
    click_times: list[float] = []

    def _run_tool(*_a, **_k):
        click_times.append(time.monotonic())
        return ok_result, "home"

    with patch(
        "navigator.agent.recorded_playback.run_tool",
        side_effect=_run_tool,
    ), patch(
        "navigator.automation.browser.verify.check",
        return_value=VerifyResult(passed=True, actual="ok"),
    ):
        outcome = run_flow_timeline(
            deps,
            session_id=uuid4(),
            page_id="home",
            flow_id="demo",
            strict=False,
        )
    assert outcome.steps_run == 1
    assert events
    assert click_times
    # No recorded speech window, so speech and click stay together on step 0.
    assert abs(events[0][1] - click_times[0]) < 0.15


def _lead_in_graph(meta: dict) -> SiteGraph:
    return SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={"signup": "text=Sign up", "confirm": "text=Confirm"},
                flows={"demo": (_click("signup"), _click("confirm"))},
            ),
        },
        meta=meta,
    )


def _run_lead_in_flow(meta: dict, *, slow_first_click_s: float = 0.0):
    """Run a 2-step flow, returning (speak times, click times) as monotonic secs."""
    speaks: list[float] = []
    clicks: list[float] = []

    class AsyncSpeaker:
        def say_async(self, text: str):
            speaks.append(time.monotonic())
            handle = MagicMock()
            handle.wait = MagicMock()
            return handle

        def say(self, text: str) -> None:
            pass

    deps = CallDeps(
        graph=_lead_in_graph(meta),
        page=MagicMock(),
        log=MagicMock(),
        speaker=AsyncSpeaker(),
        product_id="acme",
    )
    ok_result = ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1)

    def _run_tool(*_a, **_k):
        if slow_first_click_s and not clicks:
            time.sleep(slow_first_click_s)
        clicks.append(time.monotonic())
        return ok_result, "home"

    with patch(
        "navigator.agent.recorded_playback.run_tool", side_effect=_run_tool
    ), patch(
        "navigator.automation.browser.verify.check",
        return_value=VerifyResult(passed=True, actual="ok"),
    ):
        run_flow_timeline(
            deps, session_id=uuid4(), page_id="home", flow_id="demo", strict=False
        )
    return speaks, clicks


def test_timeline_speak_and_act_overlap():
    """Line and cursor start together — not talk-then-click."""
    meta = {
        "narration_suggestions": {"demo": ["Opening signup", "Now confirm"]},
        "step_clicks": {"demo": [{"idx": 0, "at_ms": 400}, {"idx": 1, "at_ms": 900}]},
        "step_speech": {
            "demo": [
                {"idx": 0, "start_ms": 100, "end_ms": 350},
                {"idx": 1, "start_ms": 600, "end_ms": 850},
            ]
        },
    }
    speaks, clicks = _run_lead_in_flow(meta)
    assert len(speaks) == 2 and len(clicks) == 2
    assert 0 <= clicks[0] - speaks[0] < 0.2
    assert 0 <= clicks[1] - speaks[1] < 0.2


def test_timeline_slip_shifts_later_cues_and_keeps_speak_act_glued():
    """A slow step pushes what follows instead of desyncing speech from action."""
    meta = {
        "narration_suggestions": {"demo": ["Opening signup", "Now confirm"]},
        "step_clicks": {"demo": [{"idx": 0, "at_ms": 300}, {"idx": 1, "at_ms": 700}]},
        "step_speech": {
            "demo": [
                {"idx": 0, "start_ms": 0, "end_ms": 250},
                {"idx": 1, "start_ms": 400, "end_ms": 650},
            ]
        },
    }
    speaks, clicks = _run_lead_in_flow(meta, slow_first_click_s=0.6)
    assert len(speaks) == 2 and len(clicks) == 2
    assert speaks[1] > clicks[0]
    assert 0 <= clicks[1] - speaks[1] < 0.2


def test_timeline_holds_later_act_until_prior_speech_ends():
    """Prefetch miss + click-before-talk metadata: long step-0 audio must gate later clicks.

    Real demos often schedule act N before speak N (host clicked, then talked). When
    TTS duration is unknown the schedule does not stretch, so act 1 fires while
    speak 0 is still describing later UI — voice ahead of the screen.
    """
    from navigator.meeting.playback_handle import PlaybackHandle

    meta = {
        "narration_suggestions": {
            "demo": [
                "Long tour of signup login google password and terms.",
                "Now the email field.",
            ]
        },
        # Cue order: speak0 → act0 → act1 → speak1 (act before speak on step 1).
        "step_clicks": {"demo": [{"idx": 0, "at_ms": 100}, {"idx": 1, "at_ms": 300}]},
        "step_speech": {
            "demo": [
                {"idx": 0, "start_ms": 0, "end_ms": 80},
                {"idx": 1, "start_ms": 500, "end_ms": 580},
            ]
        },
    }
    speech_done: list[float] = []
    clicks: list[float] = []

    class SlowAsyncSpeaker:
        def say_async(self, text: str):
            handle = PlaybackHandle()

            def worker() -> None:
                time.sleep(0.4)
                speech_done.append(time.monotonic())
                handle._finish()

            threading.Thread(target=worker, daemon=True).start()
            return handle

        def say(self, text: str) -> None:
            pass

    deps = CallDeps(
        graph=_lead_in_graph(meta),
        page=MagicMock(),
        log=MagicMock(),
        speaker=SlowAsyncSpeaker(),
        product_id="acme",
    )
    ok_result = ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1)

    def _run_tool(*_a, **_k):
        clicks.append(time.monotonic())
        return ok_result, "home"

    with patch(
        "navigator.agent.recorded_playback.run_tool", side_effect=_run_tool
    ), patch(
        "navigator.automation.browser.verify.check",
        return_value=VerifyResult(passed=True, actual="ok"),
    ):
        run_flow_timeline(
            deps, session_id=uuid4(), page_id="home", flow_id="demo", strict=False
        )

    assert len(clicks) == 2 and len(speech_done) >= 1
    # Same-step lead-in still overlaps: first click may land during speech 0.
    assert clicks[0] < speech_done[0]
    # Later step must not click while the prior line is still talking.
    assert clicks[1] >= speech_done[0] - 0.05


def test_timeline_uses_live_say_when_live_agent_present():
    """Conversational mode: recorded lines go through Live, not MeetSpeaker WAV."""
    from navigator.meeting.playback_handle import PlaybackHandle

    meta = {
        "narration_suggestions": {"demo": ["Opening the signup form."]},
        "step_clicks": {"demo": [{"idx": 0, "at_ms": 200}]},
        "step_speech": {"demo": [{"idx": 0, "start_ms": 0, "end_ms": 150}]},
    }
    said: list[str] = []
    async_calls: list[str] = []

    class LiveStub:
        def say(self, text: str, *, mode: str = "verbatim") -> None:
            said.append(f"{mode}:{text}")
            time.sleep(0.05)

    class MeetSpeakerStub:
        def say_async(self, text: str):
            async_calls.append(text)
            handle = PlaybackHandle()
            handle._finish()
            return handle

        def say(self, text: str) -> None:
            pass

    graph = SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={"signup": "text=Sign up"},
                flows={"demo": (_click("signup"),)},
            ),
        },
        meta=meta,
    )
    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=MeetSpeakerStub(),
        live_agent=LiveStub(),
        product_id="acme",
    )
    ok = ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1)
    with patch(
        "navigator.agent.recorded_playback.run_tool", return_value=(ok, "home")
    ), patch(
        "navigator.automation.browser.verify.check",
        return_value=VerifyResult(passed=True, actual="ok"),
    ):
        run_flow_timeline(
            deps, session_id=uuid4(), page_id="home", flow_id="demo", strict=False
        )
    assert async_calls == []
    assert said and said[0].startswith("natural:")
    assert "signup" in said[0].lower() or "Opening" in said[0]


def test_strict_playlist_does_not_advance_step_on_plan():
    graph = SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={"a": "text=A", "b": "text=B"},
                flows={
                    "demo": (_click("a"), _click("b")),
                },
            ),
        },
        demo_playlist=[
            DemoPlaylistItem(order=1, name="Demo", page_id="home", flow_id="demo"),
        ],
    )
    speaker = MagicMock()
    speaker.bot_ended = False
    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=speaker,
        playlist_only=True,
        strict_playlist=True,
    )
    state = initial_state(uuid4(), "home", walkthrough_flow_id="demo")
    out = planning(state, deps)
    assert out["walkthrough_step"] == 0
    assert out.get("executing_step") == 0
    assert out.get("planned_next_step") == 1


def test_decide_live_turn_blocked_on_playlist():
    from navigator.agent.nodes.planning import _decide_live_turn

    graph = SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={"signup": "text=Sign up"},
                flows={"authentication_flow": (_click("signup"),)},
            ),
        },
        demo_playlist=[
            DemoPlaylistItem(
                order=1,
                name="Authentication Flow",
                page_id="home",
                flow_id="authentication_flow",
            ),
        ],
    )
    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=MagicMock(),
        playlist_only=True,
        product_id="acme",
    )
    state = initial_state(
        uuid4(),
        "home",
        walkthrough_flow_id="authentication_flow",
    )
    assert _decide_live_turn(state, deps, utterance="show billing") is None


def test_run_flow_strict_only_yaml_steps():
    from navigator.agent.recorded_playback import run_flow_strict

    graph = SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={"a": "text=A", "b": "text=B"},
                flows={"demo": (_click("a"), _click("b"))},
            ),
        },
    )
    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=MagicMock(),
        product_id="acme",
    )
    ok = ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1)
    with patch(
        "navigator.agent.recorded_playback.run_tool",
        return_value=(ok, "home"),
    ) as run_tool_mock, patch(
        "navigator.automation.browser.verify.check",
        return_value=VerifyResult(passed=True, actual="ok"),
    ):
        outcome = run_flow_strict(
            deps,
            session_id=uuid4(),
            page_id="home",
            flow_id="demo",
            strict=True,
        )
    assert outcome.steps_run == 2
    assert run_tool_mock.call_count == 2


def test_timeline_dashboard_test_continues_on_click_fail():
    graph = SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={"a": "text=A", "b": "text=B"},
                flows={"demo": (_click("a"), _click("b"))},
            ),
        },
        meta={
            "narration_suggestions": {"demo": ["one", "two"]},
            "step_clicks": {
                "demo": [{"idx": 0, "at_ms": 0}, {"idx": 1, "at_ms": 1000}],
            },
        },
    )
    speaker = MagicMock()
    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=speaker,
        product_id="acme",
        demo_origin="dashboard_test",
    )
    ok = ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1)
    fail = ToolResult(ok=False, tool="click_element", detail="timeout", duration_ms=1)

    def _run_tool(_page, _graph, _page_id, call, **_k):
        if call.selector == "a":
            return fail, "home"
        return ok, "home"

    with patch(
        "navigator.agent.recorded_playback.run_tool",
        side_effect=_run_tool,
    ), patch(
        "navigator.automation.browser.verify.check",
        return_value=VerifyResult(passed=True, actual="ok"),
    ):
        outcome = run_flow_timeline(
            deps,
            session_id=uuid4(),
            page_id="home",
            flow_id="demo",
            strict=True,
        )
    assert outcome.steps_run == 2
    assert not outcome.hard_fail
    assert not outcome.paused
    speaker.say.assert_any_call(
        "Moving on — we'll skip that step for now."
    )


def test_timeline_skips_junk_aliases():
    graph = SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={
                    "start": "text=Start Free Trial",
                    "welcome_back": "text=Welcome back",
                    "on": "text=on",
                },
                flows={
                    "demo": (
                        _click("welcome_back"),
                        _click("on"),
                        _click("start"),
                    )
                },
            ),
        },
        meta={
            "narration_suggestions": {"demo": ["a", "b", "c"]},
            "step_clicks": {
                "demo": [
                    {"idx": 0, "at_ms": 0},
                    {"idx": 1, "at_ms": 100},
                    {"idx": 2, "at_ms": 200},
                ]
            },
        },
    )
    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=MagicMock(),
        product_id="acme",
        demo_origin="dashboard_test",
    )
    ok = ToolResult(ok=True, tool="click_element", detail="clicked", duration_ms=1)
    ran: list[str] = []

    def _run_tool(_page, _graph, _page_id, call, **_k):
        ran.append(call.selector)
        return ok, "home"

    with patch(
        "navigator.agent.recorded_playback.run_tool",
        side_effect=_run_tool,
    ), patch(
        "navigator.automation.browser.verify.check",
        return_value=VerifyResult(passed=True, actual="ok"),
    ):
        outcome = run_flow_timeline(
            deps,
            session_id=uuid4(),
            page_id="home",
            flow_id="demo",
            strict=True,
        )
    assert ran == ["start"]
    assert outcome.steps_run == 1


def test_timeline_self_visible_click_passes_without_verify_wait():
    """CTA click that removes the button must not FAIL + retry for 45s."""
    graph = SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={"start": "text=Start"},
                flows={"demo": (_click("start"),)},
            ),
        },
        meta={
            "narration_suggestions": {"demo": ["go"]},
            "step_clicks": {"demo": [{"idx": 0, "at_ms": 0}]},
        },
    )
    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=MagicMock(),
        product_id="acme",
        demo_origin="dashboard_test",
    )
    ok = ToolResult(ok=True, tool="click_element", detail="clicked", duration_ms=1)
    check_mock = MagicMock(
        return_value=VerifyResult(passed=False, actual="text=Start not found")
    )
    with patch(
        "navigator.agent.recorded_playback.run_tool",
        return_value=(ok, "home"),
    ), patch(
        "navigator.automation.browser.verify.check",
        check_mock,
    ):
        outcome = run_flow_timeline(
            deps,
            session_id=uuid4(),
            page_id="home",
            flow_id="demo",
            strict=True,
        )
    assert outcome.steps_run == 1
    assert not outcome.failures
    assert outcome.entries[0].verify.passed
    check_mock.assert_not_called()


def test_strict_verifying_pauses_on_action_fail():
    graph = SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={"a": "text=A"},
                flows={"demo": (_click("a"),)},
            ),
        },
    )
    call = _click("a")
    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=MagicMock(),
        strict_playlist=True,
        product_id="acme",
    )
    state = {
        "session_id": uuid4(),
        "page_id": "home",
        "last_page_id": "home",
        "executing_step": 0,
        "walkthrough_step": 0,
        "planned_next_step": 1,
        "last_call": call,
        "last_result": ToolResult(
            ok=False,
            tool="click_element",
            detail="timeout",
            duration_ms=15000,
        ),
        "pending_calls": [],
    }
    out = verifying(state, deps)  # type: ignore[arg-type]
    assert out["walkthrough_step"] == 0
    assert out.get("phase") == "walkthrough"
    assert out.get("failures")


def test_timeline_verify_miss_skips_that_step_speak():
    """Same-step speak starts with the click; a verify miss does not rewind it."""

    def _fill(alias: str, url: str) -> FillField:
        return FillField(
            tool="fill_field",
            selector=alias,
            value="x@y.z",
            expects=Postcondition(
                check="url_matches", expected=url, timeout_ms=100
            ),
        )

    graph = SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={"email": "#email", "next": "#next"},
                flows={"demo": (_fill("email", "/home"), _fill("next", "/dash"))},
            ),
        },
        meta={
            "narration_suggestions": {
                "demo": ["Opening signup", "Now the dashboard"],
            },
            "step_clicks": {
                "demo": [{"idx": 0, "at_ms": 0}, {"idx": 1, "at_ms": 40}],
            },
            "step_speech": {
                "demo": [
                    {"idx": 0, "start_ms": 0, "end_ms": 20},
                    {"idx": 1, "start_ms": 200, "end_ms": 240},
                ],
            },
        },
    )
    spoken: list[str] = []

    class AsyncSpeaker:
        def say_async(self, text: str):
            spoken.append(text)
            handle = MagicMock()
            handle.wait = MagicMock()
            return handle

        def say(self, text: str) -> None:
            spoken.append(text)

    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=AsyncSpeaker(),
        product_id="acme",
        demo_origin="dashboard_test",
    )
    ok = ToolResult(ok=True, tool="fill_field", detail="ok", duration_ms=1)
    checks = iter(
        [
            VerifyResult(passed=True, actual="ok"),
            VerifyResult(passed=False, actual="/dash missing"),
        ]
    )

    def _check(*_a, **_k):
        try:
            return next(checks)
        except StopIteration:
            return VerifyResult(passed=False, actual="/dash missing")

    with patch(
        "navigator.agent.recorded_playback.run_tool",
        return_value=(ok, "home"),
    ), patch(
        "navigator.automation.browser.verify.check",
        side_effect=_check,
    ):
        run_flow_timeline(
            deps, session_id=uuid4(), page_id="home", flow_id="demo", strict=False
        )

    joined = " ".join(spoken).lower()
    assert "opening signup" in joined
    # Speak+act are glued, so this step's line may already be in-flight when
    # verify misses. Later-screen leakage was the old act-before-talk bug.
