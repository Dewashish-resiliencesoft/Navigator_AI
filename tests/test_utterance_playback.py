"""Duplicate narration + barge-in confirmation + playback gating."""

from __future__ import annotations

from navigator.agent.nodes.speaking import speaking
from navigator.agent.state import CLEAR, queue
from navigator.agent.utterance import logic_id, merge_narration, stamp_narration
from navigator.voice.live_agent import LiveAgent, LiveAgentConfig


def test_queue_drops_duplicate_utterance_id():
    first = stamp_narration({"turns": 0, "walkthrough_step": 1}, ["Hello"], kind="walk")
    hindi = stamp_narration({"turns": 0, "walkthrough_step": 1}, ["नमस्ते"], kind="walk")
    assert first[0]["id"] == hindi[0]["id"]
    merged = queue(first, hindi)
    assert [x["text"] for x in merged] == ["Hello"]


def test_clear_sentinel_empties_even_after_append():
    held = stamp_narration({"turns": 1}, ["keep me"], kind="walk")
    assert queue(held, CLEAR) == []


def test_empty_list_is_not_a_clear():
    held = stamp_narration({"turns": 1}, ["keep me"], kind="walk")
    assert queue(held, []) == held


def test_speaking_reentry_does_not_replay():
    from uuid import uuid4
    from unittest.mock import MagicMock

    from navigator.agent.state import CallDeps, initial_state
    from navigator.voice.tts import PrintSpeaker

    graph = MagicMock()
    graph.pages = {}
    deps = CallDeps(
        graph=graph, page=MagicMock(), log=MagicMock(), speaker=PrintSpeaker()
    )
    state = initial_state(uuid4(), "inbox")
    state["narration"] = ["once only"]
    first = speaking(state, deps)
    assert deps.speaker.said == ["once only"]
    state["spoken_utterance_ids"] = list(first.get("spoken_utterance_ids") or [])
    speaking(state, deps)
    assert deps.speaker.said == ["once only"]


def test_logic_id_stable_across_language_text():
    state = {"walkthrough_flow_id": "demo", "walkthrough_step": 3, "turns": 2}
    assert logic_id(state, kind="walk", index=0) == logic_id(state, kind="walk", index=0)
    a = stamp_narration(state, ["English line"], kind="walk")
    b = stamp_narration(state, ["हिंदी लाइन"], kind="walk")
    assert merge_narration(a, b) == a


def _live():
    bridge = type("B", (), {"flush_bot_output": lambda self: setattr(self, "n", getattr(self, "n", 0) + 1), "n": 0})()
    cfg = LiveAgentConfig(api_key="t", system_instruction="t")
    agent = LiveAgent(cfg, bridge)
    agent.director_only = False
    return agent, bridge


def test_first_interrupt_is_probable_not_flush():
    from tests.test_live_agent import _msg

    agent, bridge = _live()
    agent.speaking = True
    agent._set_playback("playing", "u1")
    agent._handle_server_message(_msg(interrupted=True))
    assert agent.barge_in_detected == 1
    assert agent.barge_in_confirmed == 0
    assert agent.interrupted is False
    assert bridge.n == 0
    assert agent.playback_phase == "playing"


def test_confirmed_barge_in_flushes_and_stops():
    from tests.test_live_agent import _msg

    agent, bridge = _live()
    agent.speaking = True
    agent._set_playback("playing", "u1")
    agent._barge_confirm_s = 0.0
    agent._handle_server_message(_msg(interrupted=True))
    agent._handle_server_message(_msg(interrupted=True))
    assert agent.barge_in_confirmed == 1
    assert agent.interrupted is True
    assert bridge.n == 1
    assert agent.playback_phase == "ready"
    assert agent.can_start_utterance() is True


def test_overlap_blocked_until_idle_or_confirmed_barge():
    agent, _ = _live()
    agent._set_playback("playing", "u1")
    assert agent.can_start_utterance() is False
    agent._confirm_barge_in(reason="user")
    assert agent.can_start_utterance() is True


def test_set_language_does_not_ask_for_spoken_ack():
    agent, _ = _live()
    agent.set_language("hi")
    cmd = agent._cmds.get_nowait()
    assert cmd.kind == "context"
    assert "Hindi" in cmd.text
    assert "acknowledge" not in cmd.text.lower() or "not acknowledge" in cmd.text.lower() or "do not acknowledge" in cmd.text.lower()
