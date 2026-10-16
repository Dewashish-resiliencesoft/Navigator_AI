"""Step lifecycle + audio ordering: screen and voice stay in lockstep."""
from __future__ import annotations

import pathlib
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from navigator.agent.nodes import executing as ex

FIX = pathlib.Path(__file__).parent / "fixtures" / "narrated"


# ---- live agent: no duplicated fragments ---------------------------------
def _agent():
    from navigator.voice.live_agent import LiveAgent, LiveAgentConfig

    bridge = MagicMock()
    a = LiveAgent(LiveAgentConfig(api_key="t", system_instruction="t", language="en"), bridge)
    return a, bridge


def _sc(**kw):
    base = dict(interrupted=False, model_turn=None, turn_complete=False, generation_complete=False,
                input_transcription=None, output_transcription=None)
    base.update(kw)
    return SimpleNamespace(tool_call=None, session_resumption_update=None, go_away=None,
                           server_content=SimpleNamespace(**base), data=None)


def test_late_turn_complete_does_not_end_next_piece():
    a, _ = _agent()
    a._handle_server_message(_sc(generation_complete=True))
    assert a._turn_done.is_set()
    a._turn_done.clear()           # next piece starts
    a._turn_closed = False         # its audio arrived
    a._handle_server_message(_sc(turn_complete=True))  # late tc of the PREVIOUS turn
    assert not a._turn_done.is_set(), "late turn_complete cut the next line short"
    a._handle_server_message(_sc(generation_complete=True))
    assert a._turn_done.is_set()


def test_turn_complete_only_models_still_end_turns():
    a, _ = _agent()
    a._handle_server_message(_sc(turn_complete=True))
    assert a._turn_done.is_set()


def test_reply_to_refused_tool_after_line_is_muted():
    a, bridge = _agent()
    a._mute_reply = True
    part = SimpleNamespace(inline_data=SimpleNamespace(data=b"\0\0" * 480), text=None)
    a._handle_server_message(_sc(model_turn=SimpleNamespace(parts=[part])))
    bridge.push_outbound_pcm.assert_not_called()
    a._handle_server_message(_sc(generation_complete=True))
    assert a._mute_reply is False


def test_own_send_is_not_a_barge_in():
    a, bridge = _agent()
    a._in_say = True
    a._say_queued_at = time.monotonic() - 5   # outside the old 1.2s window
    a._last_voice_at = 0.0                    # prospect silent during the line
    a._interrupt_cooldown_s = 0.0
    a._handle_server_message(_sc(interrupted=True))
    a._handle_server_message(_sc(interrupted=True))
    assert a.barge_in_detected == 0 and a.barge_in_confirmed == 0


# ---- executing: ordering, two screens, frames ------------------------------
pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def page():
    import asyncio

    # an earlier test in the full suite leaks a 'running' loop marker; the sync
    # Playwright API refuses to start under it (test-only reset)
    asyncio.events._set_running_loop(None)
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium unavailable: {exc}")
        pg = b.new_page()
        yield pg
        b.close()


class Speaker:
    """Records what is on screen when each segment starts; speaking takes time."""

    def __init__(self, page, dur=0.15):
        self.page, self.dur, self.log = page, dur, []

    def say(self, text):
        self.log.append((text, self.page.url, self.page.inner_text("body")[:400]))
        time.sleep(self.dur)


def _deps(page, sp, frames):
    return SimpleNamespace(page=page, speaker=sp, graph=None, push_frame=lambda: frames.append(
        (time.monotonic(), page.url, page.inner_text("body")[:400])), live_agent=None, trace=None)


def test_narrated_click_runs_inside_step_and_frame_matches(page):
    page.goto((FIX / "inbox.html").as_uri())
    sp, frames = Speaker(page), []
    deps = _deps(page, sp, frames)
    state = {"narration": ["On the left are client chats. Let's click on a chat to open the conversation. "
                           "On the right we see the full conversation."], "executing_step": 3}
    out = ex._run_narrated_actions(state, deps, SimpleNamespace(tool="navigate", selector=""))
    assert out["narration"] == ["On the right we see the full conversation."]
    assert "Conversation with" not in sp.log[0][2]       # spoken before the click
    assert "Conversation with" in frames[-1][2]          # fresh frame shows the result before the rest is said


def test_two_screens_each_segment_matches_its_screen(page):
    sp, frames = Speaker(page), []
    deps = _deps(page, sp, frames)
    page.goto((FIX / "inbox.html").as_uri())
    ex._speak_blocking(deps, "This is the inbox.")
    page.goto((FIX / "scroll.html").as_uri())
    ex._speak_blocking(deps, "This is the long page.")
    assert "inbox.html" in sp.log[0][1] and "Select a chat" in sp.log[0][2]
    assert "scroll.html" in sp.log[1][1] and "Long page top" in sp.log[1][2]
    assert "Select a chat" in frames[0][2] and "Long page top" in frames[1][2]


def test_next_step_waits_for_previous_narration(monkeypatch):
    from navigator.core.schemas import ToolResult
    from navigator.meeting.playback_handle import PlaybackHandle

    h = PlaybackHandle()
    finished = []

    def done():
        time.sleep(0.3)
        finished.append(time.monotonic())
        h._finish()
    threading.Thread(target=done, daemon=True).start()
    acted = []

    def fake_run_tool(*a, **k):
        acted.append(time.monotonic())
        return ToolResult(ok=True, tool="click_element", detail="", duration_ms=0), "p"
    monkeypatch.setattr(ex, "run_tool", fake_run_tool)
    monkeypatch.setattr(ex, "_missing_fast", lambda *a, **k: False)
    monkeypatch.setattr(ex, "is_external_url", lambda *a, **k: False)
    monkeypatch.setattr(ex, "_tell_live_where_we_are", lambda *a, **k: None)
    pg = MagicMock(); pg.url = "https://x.test/a/"
    deps = SimpleNamespace(page=pg, graph=SimpleNamespace(base_url="https://x.test", meta={}, pages={}),
                           speaker=MagicMock(), push_frame=None, live_agent=None, trace=None,
                           product_id="p", resolve_password=None)

    class Call(SimpleNamespace):
        def model_copy(self, update=None):
            return self
    call = Call(tool="click_element", selector="#go", spoken="")
    ex.executing({"pending_calls": [call], "pre_action_speech": h, "page_id": "p", "narration": []}, deps)
    assert acted and finished and acted[0] >= finished[0], "acted while the previous line was playing"


def test_flow_holds_while_answer_audio_still_playing():
    """mark_line_end zeroes the per-line estimate; the answer is still being heard."""
    a, bridge = _agent()
    bridge._tap_play_end = 0.0
    bridge.out_play_end = time.monotonic() + 5.0
    a.speaking = False
    a._in_say = False
    a._answer_seen_at = time.monotonic() - 3.0
    a.director_only = True
    assert a.answering


def test_leftover_dialog_closed_before_navigate_step(page):
    page.set_content("<body><div role=dialog>old form</div><button>Create Contact</button></body>")
    ex._close_leftover_dialog(page)
    assert page.locator("[role=dialog]").count() == 0 or not page.locator("[role=dialog]").is_visible()
