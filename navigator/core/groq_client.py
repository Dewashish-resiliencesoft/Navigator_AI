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
