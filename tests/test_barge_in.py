"""Tests: Phase 4 - barge-in debounce and cooldown."""
from __future__ import annotations

import time
from unittest.mock import MagicMock


def _make_agent():
    from navigator.voice.live_agent import LiveAgent, LiveAgentConfig

    bridge = MagicMock()
    bridge.flush_bot_output = MagicMock()
    bridge.inbound = MagicMock()
    bridge.push_outbound_pcm = MagicMock()

    cfg = LiveAgentConfig(
        api_key="test",
        system_instruction="test",
        voice_name="Sulafat",
        language="en",
        model="gemini-3.1-flash-live-preview",
    )
    agent = LiveAgent(cfg, bridge)
    agent.director_only = False
    return agent, bridge


def _send_interrupted(agent):
    """Simulate a server interrupted message."""
    msg = MagicMock()
    sc = MagicMock()
    sc.interrupted = True
    sc.model_turn = None
    msg.session_resumption_update = None
    msg.go_away = None
    msg.server_content = sc
    msg.data = None
    agent._handle_server_message(msg)


def test_first_barge_in_confirmed():
    agent, bridge = _make_agent()
    agent._last_interrupt_time = 0.0
    _send_interrupted(agent)
    assert agent.barge_in_confirmed == 1
    assert bridge.flush_bot_output.call_count == 1


def test_duplicate_barge_in_suppressed_in_cooldown():
    agent, bridge = _make_agent()
    agent._last_interrupt_time = 0.0
    _send_interrupted(agent)
    # Immediately send another — within cooldown
    _send_interrupted(agent)
    assert agent.barge_in_confirmed == 1
    assert agent.barge_in_rejected_cooldown == 1
    assert bridge.flush_bot_output.call_count == 1  # flush only once


def test_barge_in_after_cooldown_accepted():
    agent, bridge = _make_agent()
    agent._interrupt_cooldown_s = 0.05  # short for test
    agent._last_interrupt_time = 0.0
    _send_interrupted(agent)
    time.sleep(0.1)  # wait out cooldown
    _send_interrupted(agent)
    assert agent.barge_in_confirmed == 2
    assert bridge.flush_bot_output.call_count == 2


def test_director_only_blocks_interrupt():
    agent, bridge = _make_agent()
    agent.director_only = True
    _send_interrupted(agent)
    assert agent.barge_in_confirmed == 0
    assert bridge.flush_bot_output.call_count == 0


def test_flush_count_tracked():
    agent, bridge = _make_agent()
    agent._interrupt_cooldown_s = 0.01
    agent._last_interrupt_time = 0.0
    _send_interrupted(agent)
    time.sleep(0.05)
    _send_interrupted(agent)
    assert agent.audio_flush_count == 2
