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
    # speech x2 + silence enough for ~700ms @ 32ms frames
    silence_frames = max(1, 700 // FRAME_MS)
    scores = [0.9, 0.9] + [0.1] * (silence_frames + 1)
    it = iter(scores)

    def score(_frame: bytes) -> float:
        return next(it)

    frames = [_frame(1000), _frame(1000)] + [_frame(0)] * (silence_frames + 1)
    segs = list(VoiceSegmenter(score_frame=score).segments(iter(frames)))
    assert len(segs) == 1
    # emit on Nth silence frame → speech(2) + silence(silence_frames)
    assert len(segs[0]) == BYTES_PER_FRAME * (2 + silence_frames)


def test_voice_segmenter_rebuffers_small_chunks():
    silence_frames = max(1, 700 // FRAME_MS)
    scores = [0.9, 0.9] + [0.1] * (silence_frames + 1)
    it = iter(scores)

    def score(_frame: bytes) -> float:
        return next(it)

    big = [_frame(1000), _frame(1000)] + [_frame(0)] * (silence_frames + 1)
    tiny: list[bytes] = []
    for fr in big:
        step = max(2, len(fr) // 4)
        tiny.extend(fr[i : i + step] for i in range(0, len(fr), step))
    segs = list(VoiceSegmenter(score_frame=score).segments(iter(tiny)))
    assert len(segs) == 1


def test_pcm16_to_wav_bytes_has_riff_header():
    pcm = _frame(0)
    wav = pcm16_to_wav_bytes(pcm)
    assert wav[:4] == b"RIFF"
    assert b"WAVE" in wav[:16]


def test_silero_scorer_is_cached():
    from navigator.voice.stt import _silero_scorer

    assert _silero_scorer.cache_info().maxsize == 1


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
