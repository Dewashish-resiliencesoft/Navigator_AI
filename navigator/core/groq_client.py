"""Shared Groq client.

Every live turn makes several Groq calls (STT, correction classify, planning,
phrasing). Constructing a client per call pays a fresh TLS handshake each time,
which lands directly in conversational latency.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any


@lru_cache(maxsize=16)
def groq_client(api_key: str):
    from groq import Groq

    return Groq(api_key=api_key)


def _ordered_keys(api_key: str | None) -> list[str]:
    from navigator.core.groq_keys import groq_key_candidates
    from navigator.core.key_pool import parse_key_list

    pool = groq_key_candidates()
    if api_key:
        return parse_key_list(api_key, *pool)
    return pool


def chat_completions_create(
    api_key: str | None = None, *, purpose: str = "", **kwargs
):
    """Groq chat completion with key rotation and token usage recording."""
    from navigator.core.key_pool import call_with_rotation
    from navigator.core.usage_context import record_groq_chat

    keys = _ordered_keys(api_key)
    if not keys:
        raise RuntimeError("no Groq API keys configured")

    if kwargs.get("model"):
        kwargs["model"] = resolve_model(str(kwargs["model"]), keys[0])

    def _call(key: str):
        resp = groq_client(key).chat.completions.create(**kwargs)
        record_groq_chat(resp, purpose=purpose, model=str(kwargs.get("model") or ""))
        return resp

    return call_with_rotation(_call, keys, label="groq")


def transcribe_create(api_key: str | None = None, **kwargs: Any) -> Any:
    """Groq audio transcription with key rotation."""
    from navigator.core.key_pool import call_with_rotation

    keys = _ordered_keys(api_key)
    if not keys:
        raise RuntimeError("no Groq API keys configured")

    def _call(key: str):
        return groq_client(key).audio.transcriptions.create(**kwargs)

    return call_with_rotation(_call, keys, label="groq-stt")


_AVAILABLE: list[str] | None = None
_AVAILABLE_FAILED_AT = 0.0
_FAIL_TTL_S = 300.0


def _available(key: str) -> list[str]:
    """Live Groq model ids, fetched once per process. A failed fetch is cached
    for 5 min so a Groq outage never adds a models.list round-trip per call."""
    global _AVAILABLE, _AVAILABLE_FAILED_AT
    import time as _time

    if _AVAILABLE is not None:
        return _AVAILABLE
    if _time.monotonic() - _AVAILABLE_FAILED_AT < _FAIL_TTL_S and _AVAILABLE_FAILED_AT:
        return []
    try:
        _AVAILABLE = [m.id for m in groq_client(key).models.list().data]
    except Exception:  # noqa: BLE001
        _AVAILABLE_FAILED_AT = _time.monotonic()
        return []
    return _AVAILABLE


def resolve_model(model: str, key: str) -> str:
    """Map a retired Groq model id to a live chat model of similar size.

    Groq retired llama-3.3-70b-versatile / llama-3.1-8b-instant; configs and
    saved agent settings still name them and the 404 killed a live demo.
    """
    avail = _available(key)
    if not avail or model in avail:
        return model
    chat = [m for m in avail if not any(x in m for x in ("whisper", "guard", "orpheus", "safeguard", "tts"))]
    if not chat:
        return model
    import re as _re

    size = _re.search(r"(\d+)b", model.lower())
    big = int(size.group(1)) >= 30 if size else True
    ranked = sorted(chat, key=lambda m: int((_re.search(r"(\d+)b", m.lower()) or [0, 0])[1]), reverse=big)
    pref = "openai/gpt-oss-120b" if big else "openai/gpt-oss-20b"
    pick = pref if pref in chat else ranked[0]
    print(f"[groq] model {model!r} unavailable -> using {pick!r}", flush=True)
    return pick
