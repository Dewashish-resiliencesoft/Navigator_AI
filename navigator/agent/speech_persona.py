"""Gender- and language-aware rules for spoken LLM output."""

from __future__ import annotations


def speech_rules(*, spoken_language: str, agent_gender: str) -> str:
    lang = (spoken_language or "en").strip().lower()
    gender = (agent_gender or "female").strip().lower()
    if lang == "hi":
        if gender == "male":
            return (
                "Write in natural Hindi (Devanagari). First-person voice is male — "
                "use masculine verb forms (e.g. कर रहा हूँ, दिखा रहा हूँ, बता रहा हूँ). "
                "Keep product/UI terms in English when natural for Indian users."
            )
        return (
            "Write in natural Hindi (Devanagari). First-person voice is female — "
            "use feminine verb forms (e.g. कर रही हूँ, दिखा रही हूँ, बता रही हूँ). "
            "Never use masculine forms like कर रहा हूँ. "
            "Keep product/UI terms in English when natural for Indian users."
        )
    if gender == "male":
        return (
            "Write in natural Indian English. First-person voice is male — "
            "warm, confident product specialist."
        )
    return (
        "Write in natural Indian English. First-person voice is female — "
        "warm, confident product specialist."
    )
