from navigator.meeting.intake_copy import clean_positioning, meaningful_need

DUMP = ("## ResilioHub | WhatsApp Business API & AI CRM URL: https://app.resiliohub.com/ "
        "Overview Dashboard Contacts Campaigns Inbox Settings")


def test_page_dump_not_spoken():
    out = clean_positioning(DUMP)
    assert "http" not in out and "#" not in out and "Dashboard Contacts" not in out


def test_clean_one_liner_kept():
    s = "a WhatsApp CRM that helps teams run campaigns and reply faster. More text here."
    assert clean_positioning(s) == "a WhatsApp CRM that helps teams run campaigns and reply faster"


def test_hinglish_need_dropped():
    assert meaningful_need("South Indian like bol rahi ho") == ""
    assert meaningful_need("e") == ""


def test_real_need_kept():
    assert meaningful_need("bulk WhatsApp campaigns for my clinic")


def test_unspoken_tail_detects_cut_line():
    from navigator.voice.live_agent import _unspoken_tail
    line = "let go on inbox screen in the inbox screen there is showing all chats of clients"
    assert _unspoken_tail(line, "let go").startswith("on inbox screen")
    assert _unspoken_tail(line, line) == ""
    assert _unspoken_tail(line, "") == ""


def test_nudge_only_after_delay():
    import time
    from types import SimpleNamespace
    from navigator.agent.nodes import executing as ex
    from navigator.voice.live_acks import reset_nudge_throttle_for_tests
    reset_nudge_throttle_for_tests()
    said = []
    live = SimpleNamespace(nudge=said.append, speaking=False, can_start_utterance=lambda: True)
    deps = SimpleNamespace(live_agent=live, spoken_language="en")
    ex._NUDGE_AFTER_S = 0.2
    ex._nudge_working(deps); ex._end_nudge(deps); time.sleep(0.4)
    assert said == []
    ex._nudge_working(deps); time.sleep(0.4)
    assert said and "Checking" not in said[0]
