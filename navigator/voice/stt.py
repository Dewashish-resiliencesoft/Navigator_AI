"""Speech in: VAD to find utterance boundaries, then transcription.

Silero VAD over inbound meeting audio decides when someone stopped talking;
the resulting segment goes to Groq whisper-large-v3-turbo.

Silero VAD v5 wants fixed 512-sample windows @ 16 kHz (32 ms).
"""

from __future__ import annotations

import io
import wave
from collections.abc import Callable, Iterator

SAMPLE_RATE = 16_000
# Silero VAD v5 requires exactly 512 samples @ 16 kHz (32 ms). Not 200 ms.
FRAME_MS = 32
SAMPLES_PER_FRAME = 512
BYTES_PER_FRAME = SAMPLES_PER_FRAME * 2  # 16-bit mono


class VoiceSegmenter:
    """Turns a PCM frame stream into complete utterances."""

    def __init__(
        self,
        threshold: float = 0.5,
        min_silence_ms: int = 700,
        *,
        score_frame: Callable[[bytes], float] | None = None,
    ) -> None:
        self.threshold = threshold
        self.min_silence_ms = min_silence_ms
        self._score = score_frame

    def segments(self, frames: Iterator[bytes]) -> Iterator[bytes]:
        score = self._score or _silero_scorer()
        silence_needed = max(1, self.min_silence_ms // FRAME_MS)
        buf = bytearray()
        in_speech = False
        silent_frames = 0

        for frame in _fixed_frames(frames, BYTES_PER_FRAME):
            if len(frame) == 0:
                continue
            prob = score(frame)
            speaking = prob >= self.threshold
            if speaking:
                in_speech = True
                silent_frames = 0
                buf.extend(frame)
                continue
            if in_speech:
                buf.extend(frame)
                silent_frames += 1
                if silent_frames >= silence_needed:
                    yield bytes(buf)
                    buf.clear()
                    in_speech = False
                    silent_frames = 0
        if in_speech and buf:
            yield bytes(buf)


def _fixed_frames(frames: Iterator[bytes], size: int) -> Iterator[bytes]:
    """Rebuffer arbitrary PCM chunks into fixed-size VAD windows."""
    buf = bytearray()
    for chunk in frames:
        if not chunk:
            continue
        buf.extend(chunk)
        while len(buf) >= size:
            yield bytes(buf[:size])
            del buf[:size]


def _silero_scorer() -> Callable[[bytes], float]:
    """Lazy-load Silero once. Raises if voice extra missing."""
    try:
        from silero_vad import load_silero_vad  # type: ignore[import-untyped]
        import torch
    except ImportError as e:
        raise RuntimeError(
            "silero-vad (and torch) required for live STT; pip install '.[voice]'"
        ) from e

    model = load_silero_vad()

    def score(frame: bytes) -> float:
        if len(frame) < 2:
            return 0.0
        # int16 little-endian → float tensor in [-1, 1]
        import array

        samples = array.array("h")
        samples.frombytes(frame[: len(frame) - (len(frame) % 2)])
        if not samples:
            return 0.0
        # Silero wants exactly 512 samples @ 16kHz — pad/trim one window.
        need = SAMPLES_PER_FRAME
        if len(samples) < need:
            samples.extend([0] * (need - len(samples)))
        elif len(samples) > need:
            samples = array.array("h", samples[:need])
        tensor = torch.tensor(samples, dtype=torch.float32) / 32768.0
        with torch.no_grad():
            return float(model(tensor, SAMPLE_RATE).item())

    return score


def pcm16_to_wav_bytes(pcm: bytes, *, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def transcribe(
    audio: bytes,
    api_key: str,
    *,
    model: str = "whisper-large-v3-turbo",
    language: str | None = None,
) -> str:
    """Transcribe 16-bit mono PCM (or WAV bytes) via Groq Whisper.

    Do not pin ``language`` to English. Omit it so Whisper IDs the spoken
    language; pass a BCP-47 code only when the caller already knows it.
    """
    if not api_key:
        raise RuntimeError("Groq API key missing for STT")
    payload = audio
    # Raw PCM → wrap as WAV for the multipart upload.
    if not audio[:4] == b"RIFF":
        payload = pcm16_to_wav_bytes(audio)

    from navigator.core.groq_client import transcribe_create

    kwargs: dict = {}
    if language:
        kwargs["language"] = language
    transcript = transcribe_create(
        api_key,
        file=("utterance.wav", payload, "audio/wav"),
        model=model,
        **kwargs,
    )
    text = getattr(transcript, "text", None) or str(transcript)
    return text.strip()
