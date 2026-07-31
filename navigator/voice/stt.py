"""Speech in: VAD to find utterance boundaries, then transcription.

STUB. Phase 2 fills this in.

Silero VAD (MIT, `pip install silero-vad`) over the inbound meeting audio decides
when someone stopped talking; the resulting segment goes to Groq
whisper-large-v3-turbo. Note Silero wants 150-250ms windows, not the 30ms frames
webrtcvad uses -- feeding it 30ms chunks degrades it badly.

Free-tier budget: 7200 audio-seconds per clock hour, so roughly 2h of speech per
hour. One concurrent call is comfortable; several are not.
"""

from __future__ import annotations

from collections.abc import Iterator

SAMPLE_RATE = 16_000
FRAME_MS = 200
"""Silero prefers 150-250ms. Do not drop this to 30ms."""


class VoiceSegmenter:
    """Turns a PCM frame stream into complete utterances."""

    def __init__(self, threshold: float = 0.5, min_silence_ms: int = 700) -> None:
        self.threshold = threshold
        self.min_silence_ms = min_silence_ms

    def segments(self, frames: Iterator[bytes]) -> Iterator[bytes]:
        # TODO(phase 2): silero_vad.load_silero_vad(), score each frame, emit the
        # buffered audio once silence exceeds min_silence_ms.
        raise NotImplementedError("VAD lands in Phase 2")


def transcribe(audio: bytes, api_key: str) -> str:
    # TODO(phase 2): Groq audio.transcriptions.create,
    # model="whisper-large-v3-turbo". Requests under 10s still bill at 10s, so
    # batch short utterances if the hourly audio budget gets tight.
    raise NotImplementedError("STT lands in Phase 2")
