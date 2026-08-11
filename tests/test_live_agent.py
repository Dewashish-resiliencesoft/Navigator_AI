"""LiveAgent message handling — no network, fake server messages only."""

from __future__ import annotations

from types import SimpleNamespace

from navigator.voice.live_agent import (
    OUTPUT_SAMPLE_RATE,
    LiveAgent,
    LiveAgentConfig,
    _Cmd,
    _prompt_for,
)


class FakeBridge:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, int]] = []
        self.flushes = 0

    def push_outbound_pcm(self, pcm: bytes, *, sample_rate: int = 16000) -> None:
        self.sent.append((pcm, sample_rate))

    def flush_bot_output(self) -> None:
        self.flushes += 1


def _agent(bridge: FakeBridge, events: list | None = None) -> LiveAgent:
    cfg = LiveAgentConfig(
        api_key="test",
        system_instruction="be a product specialist",
        on_event=(events.append if events is not None else None),
    )
    return LiveAgent(cfg, bridge)


def _msg(*, parts=None, interrupted=False, turn_complete=False, data=None, said=""):
    model_turn = SimpleNamespace(parts=parts or []) if parts is not None else None
    sc = SimpleNamespace(
        interrupted=interrupted,
        model_turn=model_turn,
        turn_complete=turn_complete,
        output_transcription=SimpleNamespace(text=said) if said else None,
        input_transcription=None,
    )
    return SimpleNamespace(server_content=sc, data=data)


def _audio_part(data: bytes):
    return SimpleNamespace(inline_data=SimpleNamespace(data=data))


def test_audio_parts_stream_out_at_24k():
    bridge = FakeBridge()
    agent = _agent(bridge)
    agent._handle_server_message(_msg(parts=[_audio_part(b"\x01\x02")]))
    assert bridge.sent == [(b"\x01\x02", OUTPUT_SAMPLE_RATE)]


def test_all_parts_are_read_not_just_the_first():
    bridge = FakeBridge()
    agent = _agent(bridge)
    agent._handle_server_message(
        _msg(parts=[_audio_part(b"aa"), _audio_part(b"bb")], said="hello")
    )
    assert [p for p, _ in bridge.sent] == [b"aa", b"bb"]


def test_flattened_data_is_not_played_twice():
    bridge = FakeBridge()
    agent = _agent(bridge)
    agent._handle_server_message(_msg(parts=[_audio_part(b"aa")], data=b"aa"))
    assert len(bridge.sent) == 1


def test_flattened_data_used_when_no_parts():
    bridge = FakeBridge()
    agent = _agent(bridge)
    agent._handle_server_message(_msg(parts=[], data=b"zz"))
    assert bridge.sent == [(b"zz", OUTPUT_SAMPLE_RATE)]


def test_interrupted_flushes_downstream_audio():
    bridge = FakeBridge()
    events: list = []
    agent = _agent(bridge, events)
    agent.speaking = True
    agent._handle_server_message(_msg(interrupted=True))
    assert bridge.flushes == 1
    assert agent.interrupted is True
    assert agent.speaking is False
    assert [e.kind for e in events] == ["interrupted"]


def test_interrupted_does_not_also_queue_that_turns_audio():
    bridge = FakeBridge()
    agent = _agent(bridge)
    agent._handle_server_message(
        _msg(parts=[_audio_part(b"stale")], interrupted=True)
    )
    assert bridge.sent == []


def test_turn_complete_releases_say():
    bridge = FakeBridge()
    agent = _agent(bridge)
    agent._turn_done.clear()
    agent._handle_server_message(_msg(parts=[], turn_complete=True))
    assert agent._turn_done.is_set()
    assert agent.speaking is False


def test_verbatim_and_natural_prompts_differ():
    verbatim = _prompt_for(_Cmd(kind="say", text="Hi there", mode="verbatim"))
    natural = _prompt_for(_Cmd(kind="say", text="Hi there", mode="natural"))
    assert "word for word" in verbatim
    assert "your own words" in natural
    assert "Hi there" in verbatim and "Hi there" in natural


def test_context_prompt_is_not_spoken():
    text = _prompt_for(_Cmd(kind="context", text="now on Deals page"))
    assert "do not say this out loud" in text
    assert "now on Deals page" in text


def test_resumption_handle_is_kept():
    bridge = FakeBridge()
    agent = _agent(bridge)
    msg = SimpleNamespace(
        session_resumption_update=SimpleNamespace(new_handle="tok-1"),
        go_away=None,
        server_content=None,
        data=None,
    )
    agent._handle_server_message(msg)
    assert agent._resumption_handle == "tok-1"
