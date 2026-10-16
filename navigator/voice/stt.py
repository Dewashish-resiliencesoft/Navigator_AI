"""Speech in: VAD to find utterance boundaries, then transcription.

Silero VAD over inbound meeting audio decides when someone stopped talking;
the resulting segment goes to Groq whisper-large-v3-turbo.

Silero VAD v5 wants fixed 512-sample windows @ 16 kHz (32 ms).
"""

from __future__ import annotations

import io
import threading
import wave
from collections.abc import Callable, Iterator

# One partial transcript after this much speech, while the utterance continues.
# The final segment is still yielded after silence.
# Gemini Live streams input transcription on the socket. Groq Whisper is
# request/response, so this partial is the streaming hook it supports.
# Hosted NVIDIA Parakeet is one batch request per utterance (about 40/min);
# partials are skipped there.
PARTIAL_AFTER_MS = 240

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

    def segments(
        self,
        frames: Iterator[bytes],
        *,
        on_partial: Callable[[bytes], None] | None = None,
        partial_after_ms: int = PARTIAL_AFTER_MS,
    ) -> Iterator[bytes]:
        """Yield one complete utterance after silence.

        ``on_partial`` is called once per utterance after ``partial_after_ms``
        of speech, with the audio so far. It must return quickly (callers
        transcribe on a background thread). The final yield is unchanged.
        """
        score = self._score or _silero_scorer()
        silence_needed = max(1, self.min_silence_ms // FRAME_MS)
        partial_frames = max(1, int(partial_after_ms) // FRAME_MS)
        buf = bytearray()
        in_speech = False
        silent_frames = 0
        speech_frames = 0
        partial_sent = False

        for frame in _fixed_frames(frames, BYTES_PER_FRAME):
            if len(frame) == 0:
                continue
            prob = score(frame)
            speaking = prob >= self.threshold
            if speaking:
                in_speech = True
                silent_frames = 0
                speech_frames += 1
                buf.extend(frame)
                if (
                    on_partial is not None
                    and not partial_sent
                    and speech_frames >= partial_frames
                ):
                    partial_sent = True
                    try:
                        on_partial(bytes(buf))
                    except Exception as exc:  # noqa: BLE001
                        print(f"[stt] partial hook failed: {exc}", flush=True)
                continue
            if in_speech:
                buf.extend(frame)
                silent_frames += 1
                if silent_frames >= silence_needed:
                    yield bytes(buf)
                    buf.clear()
                    in_speech = False
                    silent_frames = 0
                    speech_frames = 0
                    partial_sent = False
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


def spawn_partial_transcript(
    pcm: bytes,
    transcribe_fn: Callable[[bytes], str],
    on_text: Callable[[str], None],
) -> None:
    """Transcribe a mid-utterance buffer on a background thread.

    Skipped when NVIDIA STT is selected: that hosted tier is about 40
    requests per minute, and the final utterance is already one request.
    Default Groq STT fires one extra call so the brain can prefetch before
    silence closes the utterance.
    """
    from navigator.voice.nvidia import stt_selected

    if stt_selected() or not pcm:
        return

    def _run() -> None:
        try:
            text = (transcribe_fn(pcm) or "").strip()
        except Exception as exc:  # noqa: BLE001
            print(f"[stt] partial skipped: {exc}", flush=True)
            return
        if not text:
            return
        print(f"[stt] partial {text[:80]!r}", flush=True)
        try:
            on_text(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[stt] partial handler failed: {exc}", flush=True)

    threading.Thread(target=_run, name="stt-partial", daemon=True).start()


def meeting_stt_mode() -> str:
    """How the selected meeting STT returns text.

    ``partial`` — Groq Whisper: one mid-utterance transcript, then a final
    after ``NAVIGATOR_LIVE_STT_MIN_SILENCE_MS``. That is the streaming hook
    the request/response API supports.

    ``batch`` — hosted NVIDIA Parakeet: one request when the utterance ends.
    Partials are not sent (the tier is about 40 requests per minute).

    Gemini Live, when the mouth is connected, also streams
    ``input_audio_transcription`` on the socket. That path does not go
    through this function.
    """
    from navigator.voice.nvidia import stt_selected

    if stt_selected():
        return "batch"
    return "partial"


def transcribe(
    audio: bytes,
    api_key: str,
    *,
    model: str = "whisper-large-v3-turbo",
    language: str | None = None,
) -> str:
    """Transcribe 16-bit mono PCM (or WAV bytes).

    Default is Groq Whisper. ``NAVIGATOR_STT_PROVIDER=nvidia`` tries the
    hosted Parakeet endpoint first and falls back to Groq on 429, errors,
    or a non-English language (Parakeet's hosted model is English-only).

    Do not pin ``language`` to English on the Groq path. Omit it so Whisper
    IDs the spoken language; pass a code only when the caller already knows it.
    """
    from navigator.voice.nvidia import NvidiaVoiceError, stt_selected, transcribe_nvidia

    if stt_selected():
        try:
            return transcribe_nvidia(audio, language=language)
        except NvidiaVoiceError as exc:
            print(f"[nvidia-stt] falling back to Groq ({exc})", flush=True)
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
