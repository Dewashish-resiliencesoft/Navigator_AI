import time
from queue import Queue
from types import SimpleNamespace

from navigator.meeting import audio_bridge as ab
from navigator.voice.live_agent import LiveAgent


def test_wait_heard_blocks_until_held_audio_played():
    b = SimpleNamespace(out_play_end=time.monotonic() + 0.4, _outbound=Queue())
    ab.ACTIVE = b
    try:
        w = ab.wait_heard(cap_s=3, player_latency_s=0.1)
    finally:
        ab.ACTIVE = None
    assert 0.45 <= w < 1.0


def test_wait_heard_caps():
    ab.ACTIVE = SimpleNamespace(out_play_end=time.monotonic() + 60, _outbound=Queue())
    try:
        assert ab.wait_heard(cap_s=0.3) < 0.6
    finally:
        ab.ACTIVE = None


def _a(**kw):
    a = LiveAgent.__new__(LiveAgent)
    a.__dict__.update({"_in_say": False, "_say_ended_at": time.monotonic() - 5, "_last_heard_at": 0.0, **kw})
    return a


def test_unsolicited_model_audio_between_lines_is_dropped():
    assert _a()._unsolicited_audio() is True
    assert _a(_in_say=True)._unsolicited_audio() is False
    assert _a(_last_heard_at=time.monotonic() - 2)._unsolicited_audio() is False  # answering
    assert _a(_nudge_sent_at=time.monotonic() - 1)._unsolicited_audio() is False  # ack
    assert _a(_say_ended_at=0.0)._unsolicited_audio() is False  # before any scripted line


def test_jitter_buffer_grows_only_after_underrun():
    b = ab.AudioBridge.__new__(ab.AudioBridge)
    now = time.monotonic()
    b._line_t0 = now; b._line_rx_s = 0.0
    assert b._prebuffer_need(now + 0.1) == 0.45
    b.jitter_extra_s = 0.3
    assert abs(b._prebuffer_need(now + 0.1) - 0.75) < 1e-9
    b.mark_line_end()
    assert abs(b.jitter_extra_s - 0.25) < 1e-9
