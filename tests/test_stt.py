"""STT: VAD segmentation + transcription helpers."""

from __future__ import annotations

import struct

from navigator.voice.stt import (
    BYTES_PER_FRAME,
    FRAME_MS,
    SAMPLE_RATE,
    VoiceSegmenter,
    pcm16_to_wav_bytes,
    transcribe,
)


def _frame(amplitude: int) -> bytes:
    n = SAMPLE_RATE * FRAME_MS // 1000
    return struct.pack(f"<{n}h", *([amplitude] * n))


def test_voice_segmenter_emits_on_silence_with_injected_scorer():
    # speech x2 + silence x4; emit after 3 silent frames (700ms // 200ms)
    scores = [0.9, 0.9, 0.1, 0.1, 0.1, 0.1]
    it = iter(scores)

    def score(_frame: bytes) -> float:
        return next(it)

    frames = [_frame(1000), _frame(1000), _frame(0), _frame(0), _frame(0), _frame(0)]
    segs = list(VoiceSegmenter(score_frame=score).segments(iter(frames)))
    assert len(segs) == 1
    assert len(segs[0]) == BYTES_PER_FRAME * 5  # emit on 3rd silence frame


def test_pcm16_to_wav_bytes_has_riff_header():
    pcm = _frame(0)
    wav = pcm16_to_wav_bytes(pcm)
    assert wav[:4] == b"RIFF"
    assert b"WAVE" in wav[:16]


def test_transcribe_posts_wav_via_injected_path(monkeypatch):
    captured: dict = {}

    class FakeTranscriptions:
        def create(self, **kwargs):
            captured.update(kwargs)

            class R:
                text = "hello world"

            return R()

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeGroq:
        def __init__(self, api_key: str):
            captured["api_key"] = api_key
            self.audio = FakeAudio()

    monkeypatch.setattr("navigator.voice.stt.Groq", FakeGroq, raising=False)
    # Patch where imported inside function
    import navigator.voice.stt as stt

    monkeypatch.setattr(stt, "Groq", FakeGroq, raising=False)

    # Force import path: monkeypatch groq module used inside transcribe
    import sys
    from types import ModuleType

    fake_mod = ModuleType("groq")
    fake_mod.Groq = FakeGroq  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "groq", fake_mod)

    text = transcribe(_frame(100), "gsk_test")
    assert text == "hello world"
    assert captured["api_key"] == "gsk_test"
    name, data, mime = captured["file"]
    assert name.endswith(".wav")
    assert data[:4] == b"RIFF"
    assert mime == "audio/wav"
