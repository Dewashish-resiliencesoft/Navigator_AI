"""Round 10: jitter floor, bracket-free prompts, no screen context before the flow."""
import time
from types import SimpleNamespace

from navigator.meeting import audio_bridge
from navigator.voice import live_agent


def _bridge():
    return audio_bridge.AudioBridge.__new__(audio_bridge.AudioBridge)


def test_jitter_floor_kept_after_recent_underrun():
    b = _bridge()
    b.jitter_extra_s = 0.3
    b._last_underrun_at = time.monotonic()
    b._line_underran = False
    b.mark_line_end()
    assert b.jitter_extra_s >= 0.3


def test_jitter_decays_when_quiet():
    b = _bridge()
    b.jitter_extra_s = 0.3
    b._last_underrun_at = time.monotonic() - 300
    b._line_underran = False
    b.mark_line_end()
    assert b.jitter_extra_s < 0.3


def test_prompts_have_no_square_brackets():
    for kind, mode in [("context", None), ("nudge", None), ("say", "acknowledge"),
                       ("say", "result"), ("say", "error"), ("say", "natural")]:
        cmd = SimpleNamespace(kind=kind, mode=mode, text="hello")
        out = live_agent._prompt_for(cmd, language="en")
        assert "[" not in out and "]" not in out


def test_no_screen_inject_before_flow(monkeypatch):
    from navigator.agent.nodes import executing
    from navigator.meeting import ui_jobs
    monkeypatch.setattr(executing, "STEP_URL", "")
    monkeypatch.setitem(ui_jobs.SCREEN_SUMMARY, "text", "Dashboard ...")
    monkeypatch.setitem(ui_jobs.SCREEN_SUMMARY, "hash", "h1")
    agent = live_agent.LiveAgent.__new__(live_agent.LiveAgent)
    sent = []
    agent._in_say = False
    agent.add_context = lambda *a, **k: sent.append(a)
    agent._send_context = lambda *a, **k: sent.append(a)
    agent._inject_screen()
    assert not sent and agent.__dict__.get("_screen_sent_hash") != "h1"


def test_upsampler_seamless_across_chunks():
    import numpy as np
    from navigator.meeting.audio_bridge import Upsampler2x
    t = np.arange(24000) / 24000.0
    x = (8000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    u = Upsampler2x()
    y = np.concatenate([u.process(x[i:i + 2400]) for i in range(0, len(x), 2400)])
    whole = Upsampler2x().process(x)
    assert np.max(np.abs(y - whole)) < 1e-6  # chunking changes nothing
    d2 = np.abs(np.diff(y[200:], 2))
    assert d2.max() < 3 * np.percentile(d2, 99)  # no edge spikes
    S = np.abs(np.fft.rfft(y[1000:47000] * np.hanning(46000))) ** 2
    f = np.fft.rfftfreq(46000, 1 / 48000)
    assert S[f > 12500].sum() / S.sum() < 1e-6  # no 23.56 kHz image
