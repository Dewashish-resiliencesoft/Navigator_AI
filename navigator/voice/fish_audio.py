"""Fish Audio streaming TTS (free model only).

Verified against https://docs.fish.audio (October 2026):

- ``POST https://api.fish.audio/v1/tts``
- Header ``Authorization: Bearer $NAVIGATOR_FISH_AUDIO_API_KEY``
- Header ``model: s2.1-pro-free``. Omitting it, or sending an unrecognized
  model, serves the request as the paid ``s2.1-pro`` model. This module
  always sends the free header.
- JSON body: ``text``, ``format=pcm``, ``sample_rate`` (24000),
  ``latency=balanced`` (low-latency streaming).
- The response body is raw 16-bit mono PCM with no container. Callers
  push chunks as they arrive.
- One HTTP attempt. Callers fall back to Gemini Live. This module does
  not retry a 429.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_TTS_TIMEOUT_S = 20.0
_TEXT_LIMIT = 1800
_FREE_MODEL = "s2.1-pro-free"
_LATENCY = "balanced"
_PCM_RATES = frozenset({8000, 16000, 24000, 32000, 44100})

#: Patched in tests.
_urlopen = urlopen


class FishAudioError(RuntimeError):
    """Fish Audio TTS failed. Callers fall back to Gemini Live."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def voice_selected() -> bool:
    from navigator.core.settings import settings

    return settings.voice_provider == "fish"


def synthesize_pcm(
    text: str,
    *,
    language: str,
    on_chunk: Callable[[bytes, int], None],
    cancel: Callable[[], bool] | None = None,
) -> float:
    """Synthesize ``text`` and deliver 16-bit mono PCM via ``on_chunk``.

    ``language`` is accepted so the Speaker switch matches NVIDIA. Fish
    speaks the phrased text as written; the free model has no voice id.

    Returns seconds of audio pushed. ``cancel`` is checked between chunks
    so a barge-in stops the stream. A cancel returns the audio already
    pushed (possibly 0) and does not raise. Raises ``FishAudioError`` on
    429, transport errors, or an empty body when nothing was cancelled.
    """
    del language  # phrased text already carries the language
    from navigator.core.settings import settings

    cleaned = " ".join((text or "").split())
    if not cleaned:
        raise FishAudioError("empty text")
    if not settings.fish_audio_api_key:
        raise FishAudioError("Fish Audio API key missing")

    url = _tts_url(settings.fish_audio_base_url)
    rate = _sample_rate(settings.fish_audio_sample_rate)
    model = _model_header(settings.fish_audio_model)
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
            payload={
                "text": piece,
                "format": "pcm",
                "sample_rate": rate,
                "latency": _LATENCY,
            },
            api_key=settings.fish_audio_api_key,
            model=model,
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
        if not stopped and cancel is not None and cancel():
            stopped = True
        if stopped:
            print("[fish-tts] stopped: barge-in", flush=True)
            return total
        if pushed == 0:
            raise FishAudioError("empty audio")
        elapsed_ms = (time.monotonic() - started) * 1000.0
        print(
            f"[fish-tts] {len(piece)} chars in {elapsed_ms:.0f}ms "
            f"model={model} rate={got_rate} bytes={pushed}",
            flush=True,
        )
    if stopped:
        print("[fish-tts] stopped: barge-in", flush=True)
    return total


def _model_header(configured: str) -> str:
    """Always the free model. A blank header is billed as paid s2.1-pro."""
    chosen = (configured or "").strip()
    if chosen and chosen != _FREE_MODEL:
        print(
            f"[fish-tts] ignoring model {chosen}; sending {_FREE_MODEL}",
            flush=True,
        )
    return _FREE_MODEL


def _sample_rate(requested: int) -> int:
    rate = int(requested or 0)
    if rate in _PCM_RATES:
        return rate
    return 24_000


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
    if b.endswith("/tts"):
        return b
    return b + "/v1/tts"


def _stream_tts(
    url: str,
    *,
    payload: dict,
    api_key: str,
    model: str,
    timeout: float,
    fallback_rate: int,
    cancel: Callable[[], bool] | None = None,
):
    """Yield ``(pcm, sample_rate)`` as the HTTP body arrives."""
    resp = _open(url, payload=payload, api_key=api_key, model=model, timeout=timeout)
    try:
        pending = b""
        while True:
            if cancel is not None and cancel():
                break
            chunk = resp.read(4800)
            if not chunk:
                break
            pending += chunk
            even = pending[: len(pending) - (len(pending) % 2)]
            if even:
                yield even, fallback_rate
            pending = pending[len(even) :]
        if pending and (cancel is None or not cancel()):
            even = pending[: len(pending) - (len(pending) % 2)]
            if even:
                yield even, fallback_rate
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()


def _open(url: str, *, payload: dict, api_key: str, model: str, timeout: float):
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "model": model,
            "Accept": "*/*",
        },
    )
    try:
        return _urlopen(req, timeout=timeout)
    except HTTPError as exc:
        detail = exc.read()[:400].decode("utf-8", "replace")
        raise FishAudioError(f"HTTP {exc.code}: {detail}", status=exc.code) from exc
    except URLError as exc:
        raise FishAudioError(f"request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise FishAudioError(f"request timed out: {exc}") from exc
