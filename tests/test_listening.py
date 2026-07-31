"""LISTENING node with audio / stdin / scripted paths."""

from __future__ import annotations

import struct
from uuid import uuid4

from navigator.agent.nodes.listening import SCRIPTED_UTTERANCE, listening
from navigator.agent.state import CallDeps, initial_state
from navigator.voice.stt import FRAME_MS, SAMPLE_RATE
from navigator.voice.tts import PrintSpeaker


def _frame(amplitude: int) -> bytes:
    n = SAMPLE_RATE * FRAME_MS // 1000
    return struct.pack(f"<{n}h", *([amplitude] * n))


def test_listening_scripted_default(deps, state):
    out = listening(state, deps)
    assert out["transcript"] == [f"user: {SCRIPTED_UTTERANCE}"]
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
