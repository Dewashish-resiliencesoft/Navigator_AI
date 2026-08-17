"""Groq API keys for controller / STT / phrasing rotation."""

from __future__ import annotations

from navigator.core.key_pool import parse_key_list
from navigator.core.settings import settings


def groq_key_candidates() -> list[str]:
    return parse_key_list(settings.groq_api_key, settings.groq_api_keys)
