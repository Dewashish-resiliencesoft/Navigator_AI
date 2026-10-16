import time

from navigator.meeting import audio_bridge as ab
from navigator.voice.live_agent import LiveAgent


def test_wait_heard_waits_for_play_end():
    class B:
        out_play_end = time.monotonic() + 0.3
        import queue as _q
        _outbound = _q.Queue()
    ab.ACTIVE = B()
    try:
        t0 = time.monotonic()
        waited = ab.wait_heard(cap_s=2, player_latency_s=0.1)
        assert 0.35 <= time.monotonic() - t0 < 1.0 and waited >= 0.35
    finally:
        ab.ACTIVE = None
    assert ab.wait_heard() == 0.0


def test_unsolicited_audio_dropped_between_lines_only():
    a = LiveAgent.__new__(LiveAgent)
    now = time.monotonic()
    a.__dict__.update(_in_say=False, _say_ended_at=0.0, _last_heard_at=0.0)
    assert not a._unsolicited_audio(now)          # before the flow: intake/greeting
    a._say_ended_at = now - 5
    assert a._unsolicited_audio(now)              # chatting on its own after a line
    a._last_heard_at = now - 8  # asked mid-line, before the line ended
    assert not a._unsolicited_audio(now)          # answering the prospect
    a._last_heard_at = 0.0; a._nudge_sent_at = now - 1
    assert not a._unsolicited_audio(now)          # its 'one sec' ack
    a._nudge_sent_at = 0.0; a._in_say = True
    assert not a._unsolicited_audio(now)          # scripted line
