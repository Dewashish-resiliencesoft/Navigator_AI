"""Walkthrough must not deadlock on a stuck 'playing' phase (nudge / interrupted turn)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from navigator.voice.live_agent import LiveAgent, LiveAgentConfig


def _agent():
    bridge = SimpleNamespace(audio_s_sent=0.0, flush_bot_output=lambda: None,
                             push_outbound_pcm=lambda *a, **k: None)
    return LiveAgent(LiveAgentConfig(api_key="x", system_instruction="t"), bridge)


def _complete_turns(agent, n=3):
    def run():
        for _ in range(n * 20):
            time.sleep(0.05)
            agent._turn_done.set()
    threading.Thread(target=run, daemon=True).start()


def test_say_recovers_from_stuck_playing_after_turn_complete():
    a = _agent()
    a._set_playback("playing", "nudge")  # nudge audio left the phase here
    a.speaking = False
    a._gen_done_at = time.monotonic()
    a._drain_until = time.monotonic() + 0.2
    _complete_turns(a)
    t0 = time.monotonic()
    a.say("Step one line.", utterance_id="verifying:x:1:0:0:0")
    assert time.monotonic() - t0 < 3
    assert a.playback_phase == "ready"


def test_interrupted_say_leaves_pipeline_ready():
    a = _agent()
    _complete_turns(a)
    a._set_playback("playing", "u1")
    a.interrupted = True
    a._barge_state = "probable"
    a._gen_done_at = time.monotonic()
    a.say("Next line.", utterance_id="u2")
    assert a.playback_phase == "ready"


def test_wait_idle_hard_timeout():
    a = _agent()
    a._set_playback("playing", "u")
    a.speaking = True  # generation never reports done
    t0 = time.monotonic()
    a._wait_idle_or_recover("u", max_wait_s=0.5)
    assert 0.4 < time.monotonic() - t0 < 2
    assert a.playback_phase == "ready"


def test_barge_confirm_window_not_tiny():
    assert _agent()._barge_confirm_s >= 0.6
