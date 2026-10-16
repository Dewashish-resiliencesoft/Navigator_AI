"""Low-latency voice, page snapshot, and step sync.

These cover the live-demo stalls: the first sentence starts before the rest
of the line is prepared, barge-in stops playback, the planner sees a compact
viewport snapshot, and a failed click speaks a recovery line instead of hanging.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from navigator.agent.nodes import executing as ex
from navigator.automation.browser import tools
from navigator.automation.browser.page_snapshot import (
    PageSnapshot,
    attach_snapshot,
    measure_page,
    scroll_locator_into_view,
)
from navigator.automation.browser.tools import ACTION_TIMEOUT_CAP_MS, _action_timeout
from navigator.core.schemas import ClickElement, Postcondition, ToolResult
from navigator.voice.line_prep import lead_and_rest, speak_chunks
from navigator.voice.stt import BYTES_PER_FRAME, VoiceSegmenter


def test_lead_and_rest_speaks_the_first_sentence_alone():
    parts = lead_and_rest("Hello there. This is the second sentence.")
    assert parts[0] == "Hello there."
    assert parts[1].startswith("This is")
    assert lead_and_rest("One short line.") == ["One short line."]


def test_speak_chunks_starts_on_the_first_clause():
    parts = speak_chunks("I'll open the pricing page, then we'll look at the plans.")
    assert parts[0] == "I'll open the pricing page,"
    assert parts[1].startswith("then we'll")
    assert speak_chunks("Hello there. This is the second sentence.") == [
        "Hello there.",
        "This is the second sentence.",
    ]


def test_say_queues_the_first_clause_before_the_rest():
    from navigator.voice.live_agent import LiveAgent, LiveAgentConfig

    bridge = MagicMock()
    bridge.audio_s_sent = 0.0
    bridge.out_play_end = 0.0
    bridge._tap_play_end = 0.0
    agent = LiveAgent(LiveAgentConfig(api_key="t", system_instruction="t", language="en"), bridge)
    queued: list[str] = []

    def put(cmd):
        queued.append(cmd.text)
        agent._turn_said.append(cmd.text)
        agent._last_turn_audio_s = 3.0
        agent._turn_done.set()

    agent._cmds.put = put
    try:
        agent.say("I'll open the pricing page, then we'll look at the plans.")
    finally:
        agent._loop.close()
    assert queued[0] == "I'll open the pricing page,"
    assert queued[1].startswith("then we'll")


def test_say_queues_the_first_sentence_before_the_rest():
    from navigator.voice.live_agent import LiveAgent, LiveAgentConfig

    bridge = MagicMock()
    bridge.audio_s_sent = 0.0
    bridge.out_play_end = 0.0
    bridge._tap_play_end = 0.0
    agent = LiveAgent(LiveAgentConfig(api_key="t", system_instruction="t", language="en"), bridge)
    queued: list[str] = []

    def put(cmd):
        queued.append(cmd.text)
        agent._turn_said.append(cmd.text)
        agent._last_turn_audio_s = 3.0
        agent._turn_done.set()

    agent._cmds.put = put
    try:
        agent.say("Hello there. This is the second sentence.")
    finally:
        agent._loop.close()
    assert queued[0] == "Hello there."
    assert any(part.startswith("This is") for part in queued[1:])


def test_note_first_audio_logs_ttfa(capsys):
    from navigator.voice.live_agent import LiveAgent, LiveAgentConfig

    bridge = MagicMock()
    agent = LiveAgent(LiveAgentConfig(api_key="t", system_instruction="t"), bridge)
    try:
        agent._in_say = True
        agent._say_queued_at = time.monotonic() - 0.25
        agent._note_first_audio()
    finally:
        agent._loop.close()
    assert "ttfa_ms=" in capsys.readouterr().out


def test_recent_voice_confirms_barge_in_without_waiting_for_stt():
    from navigator.voice.live_agent import LiveAgent, LiveAgentConfig

    bridge = MagicMock()
    agent = LiveAgent(LiveAgentConfig(api_key="t", system_instruction="t"), bridge)
    try:
        agent.director_only = False
        agent._in_say = True
        agent.speaking = True
        agent._say_queued_at = time.monotonic() - 5
        agent._say_started_at = agent._say_queued_at
        agent._last_voice_at = time.monotonic()
        agent._last_heard_at = agent._last_voice_at
        agent._interrupt_cooldown_s = 0
        agent._handle_server_message(
            SimpleNamespace(
                tool_call=None,
                session_resumption_update=None,
                go_away=None,
                data=None,
                server_content=SimpleNamespace(
                    interrupted=True,
                    model_turn=None,
                    turn_complete=False,
                    generation_complete=False,
                    input_transcription=None,
                    output_transcription=None,
                ),
            )
        )
        assert agent.barge_in_confirmed == 1
        assert agent._drop_inflight_audio is True
        bridge.flush_bot_output.assert_called()
    finally:
        agent._loop.close()


def test_vad_stops_playback_when_the_user_talks_over_an_answer():
    from navigator.voice.live_agent import LiveAgent, LiveAgentConfig

    agent = LiveAgent(LiveAgentConfig(api_key="t", system_instruction="t"), MagicMock())
    try:
        agent._in_say = False
        agent.speaking = True
        agent.playback_phase = "playing"
        now = time.monotonic()
        agent._audio_rx_at = now
        agent._vad_feed(2000, now)
        assert agent.interrupted is False
        agent._vad_feed(2000, now + agent.VAD_MIN_S + 0.02)
        assert agent.interrupted is True
        assert agent.barge_in_confirmed == 1
        agent.bridge.flush_bot_output.assert_called()
    finally:
        agent._loop.close()


def test_synthesize_stops_when_cancelled(monkeypatch):
    from navigator.core.settings import settings
    from navigator.voice.nvidia import synthesize_pcm

    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")

    def stream(*_a, **_k):
        yield b"\x00\x01" * 10, 24000
        yield b"\x00\x02" * 10, 24000

    monkeypatch.setattr("navigator.voice.nvidia._stream_tts", stream)
    seen: list[bytes] = []

    total = synthesize_pcm(
        "Hello there.",
        language="en",
        on_chunk=lambda pcm, _rate: seen.append(pcm),
        cancel=lambda: len(seen) >= 1,
    )
    assert len(seen) == 1
    assert total > 0


def test_segmenter_emits_one_partial_then_the_final_utterance():
    speech = b"\x01\x00" * (BYTES_PER_FRAME // 2)
    silence = b"\x00\x00" * (BYTES_PER_FRAME // 2)
    frames = [speech] * 20 + [silence] * 30
    partials: list[bytes] = []
    seg = VoiceSegmenter(
        score_frame=lambda frame: 0.9 if frame[0] == 1 else 0.0,
        min_silence_ms=700,
    )
    done = list(seg.segments(iter(frames), on_partial=partials.append, partial_after_ms=400))
    assert len(partials) == 1
    assert len(done) == 1
    assert len(done[0]) > len(partials[0])


def test_snapshot_summary_has_viewport_cursor_and_below_fold():
    class Page:
        url = "https://example.test/inbox"
        def title(self):
            return "Inbox"
        def evaluate(self, _js):
            return {
                "vw": 1920,
                "vh": 1080,
                "sx": 0,
                "sy": 400,
                "cursor": {"x": 120, "y": 80},
                "visible": ["Inbox", "Compose"],
                "below": [{"label": "Pricing", "distance_px": 360}],
            }

    snap = measure_page(Page())
    text = snap.summary()
    assert "viewport=1920x1080" in text
    assert "scroll=(0,400)" in text
    assert "cursor=(120,80)" in text
    assert "Inbox" in text
    assert "Pricing (360px below)" in text
    assert "viewport=" in attach_snapshot(Page(), "url=already")


def test_scroll_uses_instant_scroll_not_smooth_animation():
    seen: list[str] = []
    timeouts: list[int] = []

    class Loc:
        def evaluate(self, js, timeout=None):
            seen.append(js)
            timeouts.append(timeout)
            return {"ok": True, "scrolled": True, "delta": 420, "vw": 1920, "vh": 1080}

    assert scroll_locator_into_view(None, Loc()) is True
    assert "scrollBy" in seen[0]
    assert "smooth" not in seen[0]
    assert timeouts == [1000]


def test_scroll_before_click_uses_site_graph_css_not_the_alias():
    """A step alias is not CSS. locator('send_button') waits out Playwright's 30s default."""
    from types import SimpleNamespace

    from navigator.agent.nodes.executing import _scroll_call_target

    seen: list[str] = []

    class Loc:
        def evaluate(self, js, timeout=None):
            assert timeout == 1000
            return {"ok": True, "scrolled": False}

    class Page:
        def locator(self, sel):
            seen.append(sel)
            loc = Loc()
            loc.first = loc
            return loc

    graph = SimpleNamespace(
        selector=lambda page_id, alias: {"send_button": "#send-btn", "message_input": "#message-input"}[alias]
    )
    deps = SimpleNamespace(page=Page(), graph=graph)
    _scroll_call_target(
        deps, "inbox", SimpleNamespace(tool="click_element", selector="send_button")
    )
    _scroll_call_target(
        deps, "inbox", SimpleNamespace(tool="fill_field", selector="message_input")
    )
    assert seen == ["#send-btn", "#message-input"]


def test_action_timeout_keeps_short_tests_and_caps_the_15s_default():
    assert _action_timeout(200) == 200
    assert _action_timeout(15000) == ACTION_TIMEOUT_CAP_MS
    assert ACTION_TIMEOUT_CAP_MS <= 8000


def test_click_retries_once_then_succeeds(monkeypatch):
    from playwright.sync_api import Error as PlaywrightError

    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PlaywrightError("Timeout 6000ms exceeded")
        return "clicked send", "inbox"

    monkeypatch.setitem(tools._HANDLERS, "click_element", boom)
    call = ClickElement(
        selector="send",
        expects=Postcondition(check="visible", selector="send"),
    )
    result, page_id = tools.execute(None, None, "inbox", call)
    assert result.ok
    assert page_id == "inbox"
    assert calls["n"] == 2


def test_click_retry_exhausted_is_a_result_not_a_hang(monkeypatch):
    from playwright.sync_api import Error as PlaywrightError

    def boom(*_a, **_k):
        raise PlaywrightError("Timeout 6000ms exceeded")

    monkeypatch.setitem(tools._HANDLERS, "click_element", boom)
    call = ClickElement(
        selector="send",
        expects=Postcondition(check="visible", selector="send"),
    )
    result, _page = tools.execute(None, None, "inbox", call)
    assert result.ok is False
    assert result.detail.startswith("retry exhausted:")


def test_narration_starts_with_the_action_and_failure_is_spoken(monkeypatch):
    order: list[str] = []

    def start(state, deps):
        order.append("speech")
        return SimpleNamespace(wait=lambda timeout=0: None), time.monotonic_ns()

    def run(*_a, **_k):
        order.append("tool")
        return ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1), "p"

    monkeypatch.setattr(ex, "_start_pre_action_speech", start)
    monkeypatch.setattr(ex, "run_tool", run)
    monkeypatch.setattr(ex, "_missing_fast", lambda *_a, **_k: False)
    monkeypatch.setattr(ex, "_drop_unseen_mentions", lambda state, _deps: state)
    monkeypatch.setattr(ex, "_run_narrated_actions", lambda state, _deps, _call: state)
    monkeypatch.setattr(ex, "_scroll_call_target", lambda *_a, **_k: order.append("scroll"))
    monkeypatch.setattr(ex, "is_external_url", lambda *_a, **_k: False)
    monkeypatch.setattr(ex, "_tell_live_where_we_are", lambda *_a, **_k: None)
    monkeypatch.setattr(ex, "_verify_step_screen", lambda *_a, **_k: True)
    monkeypatch.setattr(ex, "_sync_screen_to_narration", lambda *_a, **_k: None)
    monkeypatch.setattr(ex, "_component_tour", lambda *_a, **_k: None)

    class Call(SimpleNamespace):
        def model_copy(self, update=None):
            return self

    call = Call(tool="click_element", selector="#go", spoken="")
    page = MagicMock()
    page.url = "https://x.test/a"
    deps = SimpleNamespace(
        page=page,
        graph=SimpleNamespace(base_url="https://x.test", meta={}, pages={}),
        speaker=MagicMock(),
        push_frame=None,
        live_agent=None,
        trace=None,
        product_id="p",
        resolve_password=None,
    )
    ex.executing(
        {"pending_calls": [call], "page_id": "p", "narration": ["Watch this."], "session_id": "s"},
        deps,
    )
    assert order.index("scroll") < order.index("speech") < order.index("tool")

    order.clear()
    said: list[str] = []

    def fail(*_a, **_k):
        order.append("tool")
        return ToolResult(ok=False, tool="click_element", detail="Timeout 6000ms exceeded", duration_ms=1), "p"

    monkeypatch.setattr(ex, "run_tool", fail)
    deps.speaker = SimpleNamespace(say=said.append)
    ex.executing(
        {"pending_calls": [call], "page_id": "p", "narration": ["Watch this."], "session_id": "s"},
        deps,
    )
    assert any("didn't land" in line for line in said)


def test_empty_snapshot_does_not_replace_existing_screen_text():
    class Page:
        def evaluate(self, _js):
            return {}

    assert attach_snapshot(Page(), "visible=Inbox") == "visible=Inbox"
    assert PageSnapshot().summary().startswith("viewport=0x0")
