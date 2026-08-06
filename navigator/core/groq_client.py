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
