"""NVIDIA hosted speech HTTP (build.nvidia.com / Riva NIM).

Verified against the published API pages (October 2026), not guessed shapes:

TTS — Magpie multilingual, hosted NVCF function
``877104f7-e885-42b9-8de8-f6e4c6303969``
(https://build.nvidia.com/nvidia/magpie-tts-multilingual/api) and the NIM
HTTP reference (https://docs.nvidia.com/nim/speech/latest/reference/api-references/tts/http-tts.html):

- ``POST {origin}/v1/audio/synthesize_online`` streams raw 16-bit mono LPCM
  (no WAV header). ``POST .../synthesize`` returns a WAV instead.
- multipart fields: ``text``, ``language`` (BCP-47), ``voice``,
  ``encoding=LINEAR_PCM``, ``sample_rate_hz``.
- Auth: ``Authorization: Bearer $NVIDIA_API_KEY``.
- Voices: ``Magpie-Multilingual.EN-US.Aria``, Hindi
  ``Magpie-Multilingual.HI-IN.Sofia`` (Aria has no hi-IN voice).
  https://docs.nvidia.com/nim/speech/latest/tts/voices.html
- Text cap is 2,000 normalized characters. There is no ``model`` field;
  the function URL selects the model.

The OpenAI-compatible chat host (``integrate.api.nvidia.com/v1``) does not
serve this speech API. Magpie zeroshot's build.nvidia.com API page 404s and
NGC access is restricted; a self-hosted zeroshot NIM uses the same HTTP
shape with voice ``Magpie-ZeroShot-Multilingual.Female``.

STT — Parakeet CTC 1.1B, hosted function
``1598d209-5e27-4d3c-8079-4751568b1081``
(https://build.nvidia.com/nvidia/parakeet-ctc-1_1b-asr/api) and
https://docs.nvidia.com/nim/speech/latest/reference/api-references/asr/http-asr.html:

- ``POST {origin}/v1/audio/transcriptions`` multipart ``language`` + ``file``.
- JSON body ``{"text": "..."}`` (plain text also accepted).
- The hosted model card is English-only. Hindi stays on Groq.

Account trial tier is about 40 requests/minute. Callers try once and fall
back; this module does not retry a 429.
"""

from __future__ import annotations

import json
import time
import uuid
import wave
from collections.abc import Callable
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_TTS_TIMEOUT_S = 20.0
_STT_TIMEOUT_S = 20.0
_TEXT_LIMIT = 1800
_DEFAULT_EN_VOICE = "Magpie-Multilingual.EN-US.Aria"
_DEFAULT_HI_VOICE = "Magpie-Multilingual.HI-IN.Sofia"
_ZEROSHOT_VOICE = "Magpie-ZeroShot-Multilingual.Female"

#: Patched in tests.
_urlopen = urlopen


class NvidiaVoiceError(RuntimeError):
    """NVIDIA speech call failed. Callers fall back to Gemini or Groq."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def voice_selected() -> bool:
    from navigator.core.settings import settings

    return settings.voice_provider == "nvidia"


def stt_selected() -> bool:
    from navigator.core.settings import settings

    return settings.stt_provider == "nvidia"


def resolve_voice(language: str, *, model: str, voice: str, voice_hi: str) -> tuple[str, str]:
    """Return ``(bcp47, voice name)`` for a spoken language code (``en`` / ``hi``)."""
    hindi = str(language or "en").lower().startswith("hi")
    bcp = "hi-IN" if hindi else "en-US"
    chosen = (voice_hi if hindi else voice) or ""
    defaults = {_DEFAULT_EN_VOICE, _DEFAULT_HI_VOICE, ""}
    if "zeroshot" in (model or "").lower() and chosen in defaults:
        chosen = _ZEROSHOT_VOICE
    if not chosen:
        chosen = _DEFAULT_HI_VOICE if hindi else _DEFAULT_EN_VOICE
    return bcp, chosen


def synthesize_pcm(
    text: str,
    *,
    language: str,
    on_chunk: Callable[[bytes, int], None],
    cancel: Callable[[], bool] | None = None,
) -> float:
    """Synthesize ``text`` and deliver 16-bit mono PCM via ``on_chunk``.

    Returns seconds of audio pushed. ``cancel`` is checked between chunks so
    a barge-in stops the stream instead of playing out the rest of the line.
    A cancel returns the audio already pushed (possibly 0) and does not raise,
    so the caller does not also speak the line through Gemini.
    Raises ``NvidiaVoiceError`` on 429, transport errors, or an empty body
    when nothing was cancelled — no retries.
    """
    from navigator.core.settings import settings

    cleaned = " ".join((text or "").split())
    if not cleaned:
        raise NvidiaVoiceError("empty text")
    if not settings.nvidia_api_key:
        raise NvidiaVoiceError("NVIDIA API key missing")

    bcp, voice = resolve_voice(
        language,
        model=settings.nvidia_tts_model,
        voice=settings.nvidia_tts_voice,
        voice_hi=settings.nvidia_tts_voice_hi,
    )
    url = _tts_url(settings.nvidia_tts_base_url)
    rate = int(settings.nvidia_tts_sample_rate or 24_000)
    total = 0.0
    stopped = False
    for piece in _chunks(cleaned):
        if cancel is not None and cancel():
            stopped = True
            break
        started = time.monotonic()
        pushed = 0
        got_rate = rate
        for pcm, got_rate in _stream_tts(
            url,
            fields=[
                ("text", piece),
                ("language", bcp),
                ("voice", voice),
                ("encoding", "LINEAR_PCM"),
                ("sample_rate_hz", str(rate)),
            ],
            api_key=settings.nvidia_api_key,
            timeout=_TTS_TIMEOUT_S,
            fallback_rate=rate,
            cancel=cancel,
        ):
            if cancel is not None and cancel():
                stopped = True
                break
            if not pcm:
                continue
            on_chunk(pcm, got_rate)
            pushed += len(pcm)
            total += len(pcm) / float(got_rate * 2)
        if stopped:
            print("[nvidia-tts] stopped: barge-in", flush=True)
            return total
        if pushed == 0:
            raise NvidiaVoiceError("empty audio")
        elapsed_ms = (time.monotonic() - started) * 1000.0
        print(
            f"[nvidia-tts] {len(piece)} chars in {elapsed_ms:.0f}ms "
            f"voice={voice} lang={bcp} rate={got_rate} bytes={pushed}",
            flush=True,
        )
    return total


def transcribe_nvidia(audio: bytes, *, language: str | None) -> str:
    """Transcribe WAV or 16-bit PCM with the hosted Parakeet endpoint.

    English only. Hindi (and any non-``en`` language) raises so the caller
    can keep Groq, which already handles en/hi.
    """
    from navigator.core.settings import settings
    from navigator.voice.stt import SAMPLE_RATE, pcm16_to_wav_bytes

    lang = (language or settings.default_spoken_language or "en").lower()
    if not lang.startswith("en"):
        raise NvidiaVoiceError(
            f"hosted parakeet ASR is English-only (language={lang}); "
            f"model={settings.nvidia_stt_model}"
        )
    if not settings.nvidia_api_key:
        raise NvidiaVoiceError("NVIDIA API key missing")
    if not audio:
        raise NvidiaVoiceError("empty audio")

    payload = audio if audio[:4] == b"RIFF" else pcm16_to_wav_bytes(audio, sample_rate=SAMPLE_RATE)
    url = _stt_url(settings.nvidia_stt_base_url)
    started = time.monotonic()
    body = _post(
        url,
        fields=[("language", "en-US")],
        files=[("file", "utterance.wav", payload, "audio/wav")],
        api_key=settings.nvidia_api_key,
        timeout=_STT_TIMEOUT_S,
    )
    text = _transcript_text(body)
    elapsed_ms = (time.monotonic() - started) * 1000.0
    print(
        f"[nvidia-stt] {len(payload)} bytes in {elapsed_ms:.0f}ms "
        f"model={settings.nvidia_stt_model} chars={len(text)}",
        flush=True,
    )
    return text


def _chunks(text: str, limit: int = _TEXT_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buf = ""
    for sentence in _split_sentences(text):
        if buf and len(buf) + 1 + len(sentence) > limit:
            parts.append(buf)
            buf = sentence
        else:
            buf = f"{buf} {sentence}".strip()
    if buf:
        parts.append(buf)
    out: list[str] = []
    for part in parts:
        while len(part) > limit:
            out.append(part[:limit])
            part = part[limit:].strip()
        if part:
            out.append(part)
    return out or [text[:limit]]


def _split_sentences(text: str) -> list[str]:
    import re

    bits = re.split(r"(?<=[.!?])\s+", text)
    return [b for b in bits if b]


def _tts_url(base: str) -> str:
    b = (base or "").rstrip("/")
    if b.endswith("/synthesize") or b.endswith("/synthesize_online"):
        return b
    return b + "/v1/audio/synthesize_online"


def _stt_url(base: str) -> str:
    b = (base or "").rstrip("/")
    if b.endswith("/transcriptions"):
        return b
    return b + "/v1/audio/transcriptions"


def _stream_tts(
    url: str,
    *,
    fields: list[tuple[str, str]],
    api_key: str,
    timeout: float,
    fallback_rate: int,
    cancel: Callable[[], bool] | None = None,
):
    """Yield ``(pcm, sample_rate)`` as the HTTP body arrives.

    ``synthesize_online`` is raw LPCM and is forwarded in chunks.
    A WAV body (``synthesize``) is parsed once the response finishes.
    """
    resp = _open(url, fields=fields, files=[], api_key=api_key, timeout=timeout)
    try:
        pending = b""
        is_wav: bool | None = None
        wav_buf = b""
        while True:
            if cancel is not None and cancel():
                break
            chunk = resp.read(4800)
            if not chunk:
                break
            if is_wav is None:
                pending += chunk
                if len(pending) < 12:
                    continue
                is_wav = pending.startswith(b"RIFF")
                if is_wav:
                    wav_buf = pending
                    pending = b""
                    continue
                even = pending[: len(pending) - (len(pending) % 2)]
                if even:
                    yield even, fallback_rate
                pending = pending[len(even) :]
                continue
            if is_wav:
                wav_buf += chunk
            else:
                pending += chunk
                even = pending[: len(pending) - (len(pending) % 2)]
                if even:
                    yield even, fallback_rate
                pending = pending[len(even) :]
        if is_wav:
            pcm, got = _wav_pcm(wav_buf, fallback_rate=fallback_rate)
            if pcm:
                yield pcm, got
        elif is_wav is None and pending:
            even = pending[: len(pending) - (len(pending) % 2)]
            if even:
                yield even, fallback_rate
        elif pending:
            even = pending[: len(pending) - (len(pending) % 2)]
            if even:
                yield even, fallback_rate
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()


def _open(
    url: str,
    *,
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, bytes, str]],
    api_key: str,
    timeout: float,
):
    payload, content_type = _multipart(fields, files)
    req = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
            "Accept": "*/*",
        },
    )
    try:
        return _urlopen(req, timeout=timeout)
    except HTTPError as exc:
        detail = exc.read()[:400].decode("utf-8", "replace")
        raise NvidiaVoiceError(f"HTTP {exc.code}: {detail}", status=exc.code) from exc
    except URLError as exc:
        raise NvidiaVoiceError(f"request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise NvidiaVoiceError(f"request timed out: {exc}") from exc


def _post(
    url: str,
    *,
    fields: list[tuple[str, str]],
    api_key: str,
    timeout: float,
    files: list[tuple[str, str, bytes, str]] | None = None,
) -> bytes:
    resp = _open(url, fields=fields, files=files or [], api_key=api_key, timeout=timeout)
    try:
        return resp.read()
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()


def _multipart(
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, bytes, str]],
) -> tuple[bytes, str]:
    boundary = "----NavigatorNvidia" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()
        )
    for name, filename, data, mime in files:
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode()
            + data
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _pcm_from_body(body: bytes, *, fallback_rate: int) -> tuple[bytes, int]:
    if len(body) >= 12 and body[:4] == b"RIFF":
        return _wav_pcm(body, fallback_rate=fallback_rate)
    if len(body) % 2:
        body = body[:-1]
    return body, fallback_rate


def _wav_pcm(body: bytes, *, fallback_rate: int) -> tuple[bytes, int]:
    try:
        with wave.open(BytesIO(body), "rb") as wf:
            rate = wf.getframerate() or fallback_rate
            channels = wf.getnchannels() or 1
            width = wf.getsampwidth() or 2
            frames = wf.readframes(wf.getnframes())
    except wave.Error as exc:
        raise NvidiaVoiceError(f"bad wav: {exc}") from exc
    if width != 2:
        raise NvidiaVoiceError(f"expected 16-bit PCM, got {width * 8}-bit")
    if channels == 1:
        return frames, rate
    if channels == 2 and len(frames) >= 4:
        import array

        stereo = array.array("h")
        stereo.frombytes(frames[: len(frames) - (len(frames) % 4)])
        mono = array.array("h", ((stereo[i] + stereo[i + 1]) // 2 for i in range(0, len(stereo), 2)))
        return mono.tobytes(), rate
    raise NvidiaVoiceError(f"unsupported channel count {channels}")


def _transcript_text(body: bytes) -> str:
    raw = body.decode("utf-8", "replace").strip()
    if not raw:
        return ""
    if raw[0] not in "{[":
        return raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict):
        text = data.get("text")
        if isinstance(text, str):
            return text.strip()
        transcript = data.get("transcript")
        if isinstance(transcript, str):
            return transcript.strip()
        for result in data.get("results") or []:
            if not isinstance(result, dict):
                continue
            alts = result.get("alternatives") or []
            if alts and isinstance(alts[0], dict):
                spoken = alts[0].get("transcript")
                if isinstance(spoken, str) and spoken.strip():
                    return spoken.strip()
    return ""
