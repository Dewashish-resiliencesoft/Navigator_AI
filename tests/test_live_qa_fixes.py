"""13:22-13:25 Zoom run: echo of the prospect, echo-barge, tool contention, GoAway."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from navigator.voice.live_agent import LiveAgent, LiveAgentConfig, is_echo_of_heard


def _agent():
    bridge = MagicMock()
    a = LiveAgent(LiveAgentConfig(api_key="t", system_instruction="t", language="en"), bridge)
    return a, bridge


def _sc(**kw):
    base = dict(interrupted=False, model_turn=None, turn_complete=False, generation_complete=False,
                input_transcription=None, output_transcription=None)
    base.update(kw)
    return SimpleNamespace(tool_call=None, session_resumption_update=None, go_away=None,
                           server_content=SimpleNamespace(**base), data=None)


def test_never_speaks_the_prospects_words():
    heard = ["Do it a screen. I am not able to see your screen."]
    assert is_echo_of_heard("Do it a screen. I am not able to see your screen.", heard)
    assert is_echo_of_heard("a screen. I am not able to see your screen.", heard)
    assert not is_echo_of_heard("Sure, I'm sharing the Phonebook screen now.", heard)
    a, _ = _agent()
    a._heard_texts = heard
    a._cmds = MagicMock()
    a.say(heard[0])
    a._cmds.put.assert_not_called()


def test_fallback_answer_rejects_echo(monkeypatch):
    from navigator.agent import graph

    import navigator.knowledge.flow_authoring as fa
    monkeypatch.setattr(fa, "default_llm", lambda: (lambda sys, u: u))
    st = {"transcript": ["user: Do it a screen. I am not able to see your screen."], "turns": 0}
    out = graph._fallback_answer(st, SimpleNamespace(graph=None))
    assert "not able to see your screen" not in out


def test_own_audio_interrupt_during_line_is_not_a_barge():
    a, _ = _agent()
    a._in_say = True
    a._say_started_at = time.monotonic() - 2
    a._last_heard_at = 0.0
    a._last_voice_at = time.monotonic()      # mic energy / echo, no transcript
    a._say_queued_at = 0.0                   # reset by first audio (old guard missed this)
    a._interrupt_cooldown_s = 0.0
    for _ in range(3):
        a._handle_server_message(_sc(interrupted=True))
    assert a.barge_in_detected == 0


def test_muted_reply_end_does_not_end_next_line():
    a, _ = _agent()
    a._skip_next_end = True
    a._in_say = True
    a._turn_done.clear()
    a._handle_server_message(_sc(generation_complete=True))
    assert not a._turn_done.is_set()
    a._handle_server_message(_sc(generation_complete=True))
    assert a._turn_done.is_set()


def test_goaway_rotates_session_between_lines():
    a, _ = _agent()
    a._session_gen = 3
    a._goaway_gen = 3

    def bump():
        time.sleep(0.1)
        a._session_gen = 4
    threading.Thread(target=bump, daemon=True).start()
    t = time.monotonic()
    a._rotate_before_line("x")
    assert a._reconnect_req and a._session_gen == 4 and time.monotonic() - t < 2


def test_stale_show_feature_is_superseded_and_duplicates_deduped():
    from navigator.meeting import ui_jobs

    b1 = {"seq": ui_jobs._next_seq("show_feature")}
    b2 = {"seq": ui_jobs._next_seq("show_feature")}
    assert ui_jobs.superseded("show_feature", b1)["skipped"]
    assert ui_jobs.superseded("show_feature", b2) is None
    assert ui_jobs.superseded("do_task", {"seq": 0}) is None


def test_agent_runtime_yields_page_to_live_tools(monkeypatch):
    from navigator.agent_runtime import orchestrator
    from navigator.meeting import ui_jobs

    monkeypatch.setattr(ui_jobs, "ACTIVE_SCREENS", [object()])
    assert orchestrator.live_screen_tools_active()
    monkeypatch.setattr(ui_jobs, "ACTIVE_SCREENS", [])
    monkeypatch.setattr(ui_jobs, "CURRENT", None)
    assert not orchestrator.live_screen_tools_active()


def test_prompt_says_act_not_instruct():
    from navigator.meeting.ui_jobs import LIVE_TOOL_INSTRUCTION

    assert "ACT, DON'T INSTRUCT" in LIVE_TOOL_INSTRUCTION and "Manual Entry" in LIVE_TOOL_INSTRUCTION


def test_answer_start_prebuffer_is_not_too_thin():
    from navigator.meeting.audio_bridge import AudioBridge

    b = AudioBridge.__new__(AudioBridge)
    b._line_rx_s, b._line_t0, b._line_est_s = 0.05, time.monotonic(), 6.0
    assert b._prebuffer_need(time.monotonic()) >= 0.45


def test_show_feature_closes_blocking_modal_and_falls_back_to_href():
    import pytest
    pw = pytest.importorskip("playwright.sync_api")
    import asyncio

    saved = asyncio.events._get_running_loop()
    asyncio.events._set_running_loop(None)
    try:
        from navigator.meeting import ui_jobs
        with pw.sync_playwright() as p:
            try:
                b = p.chromium.launch()
            except Exception as exc:  # noqa: BLE001
                pytest.skip(str(exc))
            pg = b.new_page()
            pg.set_content("""<body><nav><a href="#phonebook" id=pb>Phonebook</a></nav>
          <div role=dialog id=d style="position:fixed;inset:0;background:#0008">form</div>
          <script>document.addEventListener('keydown',e=>{if(e.key==='Escape')d.remove()})</script></body>""")
            res = ui_jobs.show_feature(pg, "Phonebook")
            assert res["ok"], res
            assert pg.url.endswith("#phonebook")
            b.close()
    finally:
        asyncio.events._set_running_loop(saved)


def test_do_task_skips_menu_llm_when_create_button_is_here(monkeypatch):
    import pytest
    pw = pytest.importorskip("playwright.sync_api")
    import asyncio

    saved = asyncio.events._get_running_loop()
    asyncio.events._set_running_loop(None)
    try:
        from navigator.meeting import task_runner, ui_jobs
        import navigator.knowledge.flow_authoring as fa

        called = []
        monkeypatch.setattr(fa, "default_llm", lambda: (lambda *a: called.append(a) or "/x"))
        monkeypatch.setattr(ui_jobs, "NAV_INVENTORY", [{"label": "Phonebook", "href": "/phonebook"}])
        with pw.sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.set_content("<body><button>Create Contact</button></body>")
            assert task_runner._navigate_for(pg, "add a new contact", None) == ""
            assert not called
            b.close()
    finally:
        asyncio.events._set_running_loop(saved)


def test_tail_is_matched_in_order_not_as_bag_of_words():
    from navigator.voice.live_agent import _unspoken_tail

    line = ("Here we connect our API or add a new key in the Manual Setup tab. "
            "There is a button to refresh the list.")
    tail = _unspoken_tail(line, "Here we connect our API or add—")
    assert tail.startswith("a new key in the Manual Setup tab.")


def test_unbacked_action_claim_triggers_correction():
    from navigator.voice.live_agent import claims_screen_action

    assert claims_screen_action("I'm filling the form for you. Shall I save it?")
    assert claims_screen_action("I'm already on the Phonebook page. Let me show you the form.")
    assert not claims_screen_action("Phonebook stores all your contacts with their tags.")
    a, _ = _agent()
    a._cmds = MagicMock()
    a._in_say = False
    a._last_heard_at = time.monotonic() - 1
    a._last_tool_at = 0.0
    a.screen_hint = "Dashboard (/dashboard/)"
    a._answer_said = ["I'm filling the form for you, name Akash."]
    a._check_unbacked_claim()
    cmd = a._cmds.put.call_args[0][0]
    assert cmd.kind == "context" and "/dashboard/" in cmd.text and "invent" in cmd.text
    a._cmds.reset_mock()
    a._answer_said = ["I'm filling the form."]
    a._last_tool_at = time.monotonic()   # backed by a real tool call
    a._check_unbacked_claim()
    a._cmds.put.assert_not_called()


def test_qa_resume_waits_for_quiet_not_fixed_timer(monkeypatch):
    from navigator.meeting import ui_jobs

    ui = ui_jobs.UiJobs()
    ui.bind(MagicMock())
    monkeypatch.setattr(ui_jobs, "CURRENT", ui)
    monkeypatch.setattr(ui_jobs, "QUIET_S", 0.3)
    live = SimpleNamespace(answering=True)
    t0 = time.monotonic()
    threading.Timer(0.6, lambda: setattr(live, "answering", False)).start()
    ui_jobs.service_before_step(SimpleNamespace(page=ui.page, push_frame=None, live_agent=live), max_hold_s=0.2)
    took = time.monotonic() - t0
    assert 0.85 <= took < 3.0, took   # held past the old fixed cap until answer + quiet


def test_runtime_drops_sentences_naming_controls_not_on_screen():
    from navigator.agent.nodes.executing import unseen_mention_filter

    kept, dropped = unseen_mention_filter(
        "In WordPress Connect we generate a token. In Shopify Connect we connect our shop.",
        ["Shopify Connect", "WordPress Connect"], "WordPress Connect  Generate token")
    assert kept == "In WordPress Connect we generate a token." and dropped == ["Shopify Connect"]


def test_scripted_line_dropped_if_qa_moved_screen_while_waiting(monkeypatch):
    a, _ = _agent()
    a._cmds = MagicMock()
    monkeypatch.setattr(a, "can_start_utterance", lambda: False)
    monkeypatch.setattr(a, "_wait_idle_or_recover", lambda uid: setattr(a, "_last_tool_at", time.monotonic()))
    a.say("In WordPress Connect we generate a site token and link our website.")
    a._cmds.put.assert_not_called()


def test_do_task_never_types_invented_details():
    from navigator.meeting.task_runner import unspoken_values

    heard = ["You create one."]
    assert set(unspoken_values({"first name": "John", "mobile": "1234567890"}, heard)) == {"first name", "mobile"}
    heard = ["my name is Akash and mobile 98765 43210"]
    assert unspoken_values({"first name": "Akash", "mobile": "+91 9876543210"}, heard) == []


def test_skip_flag_never_swallows_a_qa_answer_end():
    a, _ = _agent()
    a._skip_next_end = True
    a._in_say = False
    a._turn_done.clear()
    a._handle_server_message(_sc(generation_complete=True))
    assert a._turn_done.is_set()


def test_answering_recovers_when_turn_end_is_lost():
    a, _ = _agent()
    a.speaking, a._in_say = True, False
    a._audio_rx_at = time.monotonic() - 20
    a._answer_seen_at = 0.0
    a._last_heard_at = 0.0
    a._last_voice_at = 0.0
    a.bridge._tap_play_end = 0.0
    a.bridge.out_play_end = 0.0
    assert not a.answering
