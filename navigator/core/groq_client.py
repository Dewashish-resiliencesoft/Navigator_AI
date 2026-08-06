"""Shared Groq client.

Every live turn makes several Groq calls (STT, correction classify, planning,
phrasing). Constructing a client per call pays a fresh TLS handshake each time,
which lands directly in conversational latency.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=2)
def groq_client(api_key: str):
    from groq import Groq

    return Groq(api_key=api_key)


def chat_completions_create(api_key: str, *, purpose: str = "", **kwargs):
    """Groq chat completion with token usage recording when demo context is bound."""
    resp = groq_client(api_key).chat.completions.create(**kwargs)
    from navigator.core.usage_context import record_groq_chat

    record_groq_chat(resp, purpose=purpose, model=str(kwargs.get("model") or ""))
    return resp
