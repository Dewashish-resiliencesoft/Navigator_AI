"""WAV duration — Fish streaming WAV has bogus data-chunk size."""

from __future__ import annotations

import io
import struct
import wave

from navigator.meeting.meet_speaker import wav_duration_s


def _normal_wav(seconds: float = 0.5, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    n = int(seconds * rate)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * n)
    return buf.getvalue()


def _fish_style_streaming_wav(*, pcm_bytes: int = 88200, rate: int = 44100) -> bytes:
    """RIFF/WAVE with fmt + data size = 0xFFFFFFFF (Fish free API style)."""
    pcm = b"\x00\x00" * (pcm_bytes // 2)
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    # Chunk sizes marked unknown / max — Python wave then reports huge nframes.
    out = io.BytesIO()
    out.write(b"RIFF")
    out.write(struct.pack("<I", 0xFFFFFFFF))
    out.write(b"WAVE")
    out.write(b"fmt ")
    out.write(struct.pack("<I", 16))
    out.write(fmt)
    out.write(b"data")
    out.write(struct.pack("<I", 0xFFFFFFFF))
    out.write(pcm)
    return out.getvalue()


def test_wav_duration_normal():
    d = wav_duration_s(_normal_wav(0.5, 16000))
    assert 0.45 <= d <= 0.55


def test_wav_duration_fish_streaming_not_hours():
    # 1 second of 44.1kHz mono 16-bit ≈ 88200 bytes
    wav = _fish_style_streaming_wav(pcm_bytes=88200, rate=44100)
    d = wav_duration_s(wav)
    assert 0.8 <= d <= 1.3, f"got {d}"
