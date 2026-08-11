"""LISTENING node with audio / stdin / scripted paths."""

from __future__ import annotations

import struct
from uuid import uuid4

from navigator.agent.nodes.listening import SCRIPTED_UTTERANCE, listening, _from_live
from navigator.agent.state import CallDeps, initial_state
from navigator.voice.stt import FRAME_MS, SAMPLE_RATE
from navigator.voice.tts import PrintSpeaker


def _frame(amplitude: int) -> bytes:
    n = SAMPLE_RATE * FRAME_MS // 1000
    return struct.pack(f"<{n}h", *([amplitude] * n))


def test_listening_walkthrough_non_interactive_continues(deps, state):
    """No STT → empty utterance so planning advances walkthrough (not a fake ask)."""
    state["phase"] = "walkthrough"
    out = listening(state, deps)
    assert out["transcript"] == ["user: "]
    assert out.get("user_correction") is False


def test_listening_legacy_scripted_when_no_walkthrough_phase(deps, state):
    state["phase"] = ""
    out = listening(state, deps)
    assert out["transcript"] == [f"user: {SCRIPTED_UTTERANCE}"]


def test_listening_anything_else_non_interactive_returns_empty(deps, state):
    state["phase"] = "anything_else"
    out = listening(state, deps)
    assert out["transcript"] == ["user: "]
    assert out.get("user_correction") is False


def test_listening_uses_audio_frames_and_injected_transcribe(
    site_graph, page, log, tmp_path
):
    scores = [0.9, 0.1, 0.1, 0.1, 0.1]
    it = iter(scores)

    # VoiceSegmenter is constructed inside listening without score_frame —
    # so inject via patching VoiceSegmenter OR feed frames that go through
    # a custom segmenter by mocking. Easier: patch VoiceSegmenter.segments.
    frames = [_frame(1000), _frame(0), _frame(0), _frame(0), _frame(0)]

    deps = CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        archive_dir=tmp_path / "archives",
        audio_frames=iter(frames),
        transcribe_audio=lambda pcm: "show me send message",
    )

    # Without score injection Silero may be missing — patch VoiceSegmenter.
    from unittest.mock import patch

    def fake_segments(self, frames_iter):
        yield b"\x00\x01" * 100

    with patch(
        "navigator.agent.nodes.listening.VoiceSegmenter.segments", fake_segments
    ):
        out = listening(initial_state(uuid4(), "inbox"), deps)
    assert out["transcript"] == ["user: show me send message"]


def test_from_live_drains_pending_and_skips_echo(site_graph, page, log, tmp_path):
    pending = ["  what is that?  "]
    deps = CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        archive_dir=tmp_path / "archives",
        live_agent=object(),
        pending_barge_in=pending,
        is_bot_echo=lambda t: False,
    )
    assert _from_live(deps, silence_timeout=0.2) == "what is that?"
    assert pending == []


def test_from_live_times_out_when_silent(site_graph, page, log, tmp_path):
    deps = CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        archive_dir=tmp_path / "archives",
        live_agent=object(),
        pending_barge_in=[],
    )
    assert _from_live(deps, silence_timeout=0.15) == ""


def test_listening_with_live_agent_uses_pending_not_whisper(
    site_graph, page, log, tmp_path
):
    deps = CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        archive_dir=tmp_path / "archives",
        live_agent=object(),
        pending_barge_in=["show me deals"],
        audio_frames=iter([b"\x00\x01"]),
        transcribe_audio=lambda pcm: "SHOULD NOT RUN",
    )
    out = listening(initial_state(uuid4(), "inbox"), deps)
    assert out["transcript"] == ["user: show me deals"]
