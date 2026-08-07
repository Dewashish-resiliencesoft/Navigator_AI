"""Groq API key lists for controller vs analysis pools."""

from __future__ import annotations

from navigator.core.key_pool import parse_key_list
from navigator.core.settings import settings


def groq_key_candidates(*, purpose: str = "default") -> list[str]:
    """Primary controller/STT/phrasing pool, or analysis pool when set."""
    if purpose == "analysis":
        analysis = parse_key_list(settings.groq_api_keys_analysis)
        if analysis:
            return analysis
    return parse_key_list(settings.groq_api_key, settings.groq_api_keys)


def groq_analysis_key_candidates() -> list[str]:
    return groq_key_candidates(purpose="analysis")
