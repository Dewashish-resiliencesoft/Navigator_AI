import time
from types import SimpleNamespace

from navigator.agent.nodes.executing import unseen_mention_filter
from navigator.meeting import ui_jobs
from navigator.voice.live_agent import LiveAgent, _sentence_tail


def test_fuzzy_screen_resolution():
    m = {"Local Sheets": "/local-sheets/", "Integrations": "/automation/integrations/", "Phonebook": "/phonebook/"}
    assert ui_jobs.resolve_screen("Local Seat", m) == ("Local Sheets", "/local-sheets/")
    assert ui_jobs.resolve_screen("integrations", m)[1] == "/automation/integrations/"
    assert ui_jobs.resolve_screen("Phone book", m)[0] == "Phonebook"
    assert ui_jobs.resolve_screen("Billing", m) == ("", "")


def test_screen_map_ignores_patterns():
    g = SimpleNamespace(pages={"a": SimpleNamespace(name="Chat", url="/chat/{id}/"),
                               "b": SimpleNamespace(name="Inbox", url="/inbox/")})
    ui_jobs.NAV_INVENTORY = [{"label": "Local Sheets", "href": "/local-sheets/"}]
    try:
        sm = ui_jobs.build_screen_map(g)
    finally:
        ui_jobs.NAV_INVENTORY = []
    assert sm == {"Local Sheets": "/local-sheets/", "Inbox": "/inbox/"}


def test_sentence_tail_backs_up_to_sentence():
    t = "First we open Inbox. Then we pick a chat and reply."
    assert _sentence_tail(t, "First we open Inbox. Then we pick") == "Then we pick a chat and reply."
    assert _sentence_tail(t, t) == ""
    assert _sentence_tail("One two.", "") == "One two."


def test_filter_exempts_current_screen_and_ignores_sidebar_only():
    labels = ["Tutorials", "Lead Forms"]
    main = "Connect your Facebook lead   forms here"
    out, dropped = unseen_mention_filter("Here are your Lead Forms. Tutorials help you learn.", labels, main,
                                "/facebook-lead-forms/")
    assert "Lead Forms" in out and "Tutorials" not in out and dropped == ["Tutorials"]


def _agent():
    a = LiveAgent.__new__(LiveAgent)
    a.__dict__.update(
        _in_say=True,
        speaking=True,
        interrupted=False,
        playback_phase="playing",
        _say_queued_at=time.monotonic() - 1,
    )
    a.hits = []
    a._confirm_barge_in = lambda reason="": a.hits.append(reason) or setattr(a, "interrupted", True)
    return a


def test_vad_stops_playback_on_sustained_voice_only():
    a = _agent(); t = time.monotonic()
    a._vad_feed(3000, t); a._vad_feed(100, t + 0.4); a._vad_feed(3000, t + 0.8)
    assert a.hits == []  # a blip, then a fresh run
    a._vad_feed(3000, t + 1.0); a._vad_feed(3000, t + 1.2)
    assert a.hits == ["vad"]
    a._vad_feed(3000, t + 1.5)
    assert a.hits == ["vad"]  # once per line


def test_vad_ignored_when_not_speaking_a_line():
    a = _agent(); a._in_say = False; a.speaking = False; t = time.monotonic()
    for k in range(10):
        a._vad_feed(3000, t + k * 0.1)
    assert a.hits == []


def test_vad_false_alarm_waits_for_late_transcript():
    import threading
    a = _agent(); a._stop = threading.Event()
    a._pumped_wait = lambda ev, t: time.sleep(t)
    now = time.monotonic(); a._vad_cut_at = now; a._vad_last = now; a._last_heard_at = 0.0
    threading.Timer(1.0, lambda: setattr(a, "_last_heard_at", time.monotonic())).start()
    assert a._vad_false_alarm(now - 1, wait_s=2.0) is False
    a._vad_cut_at = time.monotonic(); a._vad_last = a._vad_cut_at; a._last_heard_at = 0.0
    assert a._vad_false_alarm(a._vad_cut_at - 1, wait_s=0.5) is True


def test_backstop_opens_known_screen_when_live_makes_no_tool_call(monkeypatch):
    import queue, threading
    ui_jobs.SCREEN_MAP = {"Local Sheets": "/local-sheets/"}
    calls = []
    a = LiveAgent.__new__(LiveAgent)
    a._stop = threading.Event(); a._cmds = queue.Queue(); a._last_tool_at = 0.0
    a.cfg = SimpleNamespace(tool_handler=lambda n, args: calls.append((n, args)) or {"ok": True})
    try:
        a._backstop_open("Can you open Local Seats?", wait_s=0.2)
        a._backstop_open("Does this work on mobile?", wait_s=0.2)
        time.sleep(0.6)
    finally:
        ui_jobs.SCREEN_MAP = {}
    assert calls == [("show_feature", {"target": "Local Sheets"})]
    assert a._cmds.get_nowait().text == "Sure, opening Local Sheets now."


def test_backstop_ignores_how_to_and_partial_names():
    import queue, threading
    ui_jobs.SCREEN_MAP = {"Contact": "/contact/", "Inbox": "/inbox/"}
    calls = []
    a = LiveAgent.__new__(LiveAgent)
    a._stop = threading.Event(); a._cmds = queue.Queue(); a._last_tool_at = 0.0
    a.cfg = SimpleNamespace(tool_handler=lambda n, args: calls.append(n) or {"ok": True})
    try:
        a._backstop_open("Can you show me how to add a new contact here?", wait_s=0.1)
        a._backstop_open("Can you open the first chat in the inbox for me?", wait_s=0.1)
        time.sleep(0.4)
    finally:
        ui_jobs.SCREEN_MAP = {}
    assert calls == []
