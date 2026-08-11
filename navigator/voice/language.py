"""Spoken language detection for live demos (English default, Hindi on request)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

SpokenLanguage = Literal["en", "hi"]

#: BCP-47 codes for Gemini Live speech_config.
LANGUAGE_CODES: dict[SpokenLanguage, str] = {
    "en": "en-US",
    "hi": "hi-IN",
}

#: One-line acknowledgment when the prospect switches language.
SWITCH_ACK: dict[SpokenLanguage, str] = {
    "hi": "ठीक है — अब Hindi में बात करती हूँ।",
    "en": "Sure — I'll continue in English.",
}

_HINDI_SWITCH = re.compile(
    r"(?:"
    r"talk\s+to\s+me\s+in\s+hindi|"
    r"speak\s+to\s+me\s+in\s+hindi|"
    r"say\s+(?:this|it|that)\s+in\s+hindi|"
    r"please\s+(?:talk|speak|say)\s+(?:to\s+me\s+)?in\s+hindi|"
    r"hindi\s+mein\s+baat\s+karo|"
    r"hindi\s+mein\s+baat|"
    r"speak\s+hindi|"
    r"talk\s+in\s+hindi|"
    r"can\s+we\s+(?:talk|speak|communicate)\s+in\s+hindi|"
    r"hindi\s+me\s+bol|"
    r"hindi\s+mein\s+bolo|"
    r"hindi\s+me(?:in|)?|"
    r"\bhindi\b|"
    r"हिंदी|"
    r"हिन्दी"
    r")",
    re.IGNORECASE,
)

_ENGLISH_SWITCH = re.compile(
    r"(?:"
    r"english\s+please|"
    r"in\s+english|"
    r"speak\s+english|"
    r"switch\s+(?:back\s+)?to\s+english|"
    r"back\s+to\s+english|"
    r"angrezi|"
    r"अंग्रेज़ी"
    r")",
    re.IGNORECASE,
)


def detect_language_switch(utterance: str) -> SpokenLanguage | None:
    """Return target language if the prospect asked to switch, else None."""
    text = (utterance or "").strip()
    if not text:
        return None
    if _ENGLISH_SWITCH.search(text):
        return "en"
    if _HINDI_SWITCH.search(text):
        return "hi"
    return None


def language_code(lang: SpokenLanguage) -> str:
    return LANGUAGE_CODES.get(lang, "en-US")


def is_language_switch_only(utterance: str) -> bool:
    """True when the utterance is only asking to change language."""
    if detect_language_switch(utterance) is None:
        return False
    cleaned = utterance
    for pat in (_HINDI_SWITCH, _ENGLISH_SWITCH):
        cleaned = pat.sub("", cleaned)
    return len(cleaned.strip()) < 12


def apply_to_speakers(lang: SpokenLanguage, *speakers: object) -> None:
    for sp in speakers:
        if sp is not None and hasattr(sp, "set_language"):
            sp.set_language(lang)  # type: ignore[attr-defined]


def sync_speaker_language(
    speaker: object,
    utterance: str,
    *,
    current: SpokenLanguage,
) -> SpokenLanguage:
    """Switch TTS language from utterance; return effective language."""

    def _on_switch(lang: SpokenLanguage) -> None:
        apply_to_speakers(lang, speaker)

    new_lang, _ = apply_language_switch(
        utterance=utterance,
        current=current,
        on_switch=_on_switch,
    )
    return new_lang


def sync_call_language(deps: object, utterance: str) -> SpokenLanguage:
    """Apply language switch from utterance onto CallDeps + speakers."""
    current: SpokenLanguage = getattr(deps, "spoken_language", "en") or "en"
    extra = getattr(deps, "extra_languages", ("hi",)) or ("hi",)
    allowed = frozenset({current, *extra})

    def _on_switch(lang: SpokenLanguage) -> None:
        setattr(deps, "spoken_language", lang)
        speaker = getattr(deps, "speaker", None)
        synth = getattr(speaker, "synthesizer", None) if speaker is not None else None
        local = getattr(speaker, "local", None) if speaker is not None else None
        live = getattr(deps, "live_agent", None)
        apply_to_speakers(lang, speaker, synth, local, live)

    new_lang, _ = apply_language_switch(
        utterance=utterance,
        current=current,
        on_switch=_on_switch,
        allowed=allowed,
    )
    return new_lang


def apply_language_switch(
    *,
    utterance: str,
    current: SpokenLanguage,
    on_switch: Callable[[SpokenLanguage], None],
    allowed: frozenset[SpokenLanguage] | None = None,
) -> tuple[SpokenLanguage, str | None]:
    """Apply a language switch. Returns (language, optional ack line)."""
    target = detect_language_switch(utterance)
    if target is None or target == current:
        return current, None
    if allowed is not None and target not in allowed:
        return current, None
    on_switch(target)
    ack = SWITCH_ACK.get(target) if is_language_switch_only(utterance) else None
    return target, ack


def poll_barge_in_language_switch(deps: object) -> SpokenLanguage | None:
    """During timeline/TTS wait: apply switch from barge-in buffer."""
    pending = getattr(deps, "pending_barge_in", None)
    if not pending:
        return None
    current: SpokenLanguage = getattr(deps, "spoken_language", "en") or "en"
    extra = getattr(deps, "extra_languages", ("hi",)) or ("hi",)
    allowed = frozenset({current, *extra})
    speaker = getattr(deps, "speaker", None)
    switched: SpokenLanguage | None = None

    def _apply(lang: SpokenLanguage) -> None:
        setattr(deps, "spoken_language", lang)
        synth = getattr(speaker, "synthesizer", None) if speaker is not None else None
        local = getattr(speaker, "local", None) if speaker is not None else None
        live = getattr(deps, "live_agent", None)
        apply_to_speakers(lang, speaker, synth, local, live)

    while pending:
        raw = pending.pop(0)
        utterance = (raw or "").strip() if isinstance(raw, str) else ""
        if not utterance:
            continue
        new_lang, ack = apply_language_switch(
            utterance=utterance,
            current=current,
            on_switch=_apply,
            allowed=allowed,
        )
        if new_lang == current:
            continue
        current = new_lang
        switched = new_lang
        if ack and speaker is not None and hasattr(speaker, "say"):
            try:
                speaker.say(ack)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
        break
    return switched
