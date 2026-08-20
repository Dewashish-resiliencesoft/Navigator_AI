"""Utterance-level language with hysteresis, mixed-speech, TTS fallback.

Gemini Live reliably mouths ``en`` and ``hi`` only. Other detections stay on
``user_language`` for the UI; narration/TTS keep the last supported language.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from navigator.voice.language import detect_language_switch, detect_spoken_language

TTS_LANGUAGES: frozenset[str] = frozenset({"en", "hi"})
_STREAK_NEED = 2
_HIGH_CONF = 0.85
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_SPANISH = re.compile(
    r"[¿¡ñáéíóúüÑÁÉÍÓÚÜ]|"
    r"\b(?:el|la|los|las|un|una|que|por|para|con|mostrarme|puedes|hola)\b",
    re.IGNORECASE,
)
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")


@dataclass
class ConversationLanguage:
    user_language: str = "en"
    narration_language: str = "en"
    confidence: float = 1.0
    source: str = "session"
    pending_language: str = ""
    pending_count: int = 0
    locked: bool = False
    tts_fallback: bool = False

    def as_state(self) -> dict[str, object]:
        return {
            "user_language": self.user_language,
            "narration_language": self.narration_language,
            "language_confidence": self.confidence,
            "language_source": self.source,
            "language_locked": self.locked,
        }


def tts_language(code: str) -> tuple[str, bool]:
    """Return (tts_code, used_fallback)."""
    lang = (code or "en").strip().lower() or "en"
    if lang in TTS_LANGUAGES:
        return lang, False
    return "en", True


def score_utterance(text: str) -> tuple[str | None, float, str]:
    raw = (text or "").strip()
    if not raw:
        return None, 0.0, "empty"
    switched = detect_language_switch(raw)
    if switched:
        return switched, 1.0, "switch"
    latin = _LATIN_WORD.findall(raw)
    has_deva = bool(_DEVANAGARI.search(raw))
    if has_deva and len(latin) >= 3:
        return None, 0.35, "mixed"
    if has_deva:
        letters = len(_DEVANAGARI.findall(raw))
        conf = 0.92 if letters >= 8 else 0.62
        return "hi", conf, "script"
    if _SPANISH.search(raw) and len(latin) >= 3:
        return "es", 0.78, "script"
    inferred = detect_spoken_language(raw)
    if inferred == "en":
        return "en", (0.88 if len(latin) >= 6 else 0.72), "script"
    if inferred:
        return inferred, 0.72, "script"
    return None, 0.0, "ambiguous"


def observe_user_utterance(
    text: str,
    snap: ConversationLanguage,
    *,
    is_echo: bool = False,
    bot_speaking: bool = False,
    allowed: frozenset[str] | None = None,
) -> ConversationLanguage:
    """Update conversation language from one finished user utterance.

    Echo / own TTS is never evidence. Mixed tokens do not flip. Weak script
    hits need two utterances in a row; explicit switch keywords apply now.
    """
    if is_echo or bot_speaking:
        return replace(snap, source="echo_ignored")
    lang, conf, source = score_utterance(text)
    if lang is None:
        return replace(snap, source=source, pending_language="", pending_count=0)

    allow = allowed or (TTS_LANGUAGES | {snap.user_language, snap.narration_language})
    tts_code, fallback = tts_language(lang)
    narration = tts_code if lang in TTS_LANGUAGES else snap.narration_language
    if lang not in TTS_LANGUAGES:
        narration = tts_code
        fallback = True

    if source == "switch" and (lang in allow or lang in TTS_LANGUAGES):
        return ConversationLanguage(
            user_language=lang if lang in TTS_LANGUAGES else snap.user_language,
            narration_language=narration if lang in TTS_LANGUAGES else snap.narration_language,
            confidence=1.0,
            source="switch",
            tts_fallback=fallback if lang not in TTS_LANGUAGES else False,
        )

    if lang == snap.user_language and (not fallback or lang not in TTS_LANGUAGES):
        if lang in TTS_LANGUAGES:
            return replace(
                snap,
                narration_language=lang,
                confidence=max(snap.confidence, conf),
                source=source,
                pending_language="",
                pending_count=0,
                tts_fallback=False,
            )
        return replace(
            snap,
            user_language=lang,
            confidence=conf,
            source=source,
            tts_fallback=True,
            pending_language="",
            pending_count=0,
        )

    if source == "mixed":
        return replace(snap, source="mixed", confidence=min(snap.confidence, conf))

    if conf >= _HIGH_CONF and lang in TTS_LANGUAGES:
        return ConversationLanguage(
            user_language=lang,
            narration_language=lang,
            confidence=conf,
            source=source,
            tts_fallback=False,
        )

    if lang == "es":
        return ConversationLanguage(
            user_language="es",
            narration_language=snap.narration_language if snap.narration_language in TTS_LANGUAGES else "en",
            confidence=conf,
            source=source,
            tts_fallback=True,
            pending_language="",
            pending_count=0,
        )

    pending = snap.pending_language
    count = snap.pending_count
    if lang == pending:
        count += 1
    else:
        pending = lang
        count = 1
    if count >= _STREAK_NEED and lang in TTS_LANGUAGES:
        return ConversationLanguage(
            user_language=lang,
            narration_language=lang,
            confidence=conf,
            source=source,
            tts_fallback=False,
        )
    return replace(
        snap,
        pending_language=pending,
        pending_count=count,
        source=source,
        confidence=snap.confidence,
    )


def publish_speech(
    deps: object,
    *,
    status: str,
    narration: str = "",
    state: dict | None = None,
) -> dict[str, object]:
    """Push live Demo Script fields to the dashboard poll."""
    snap = getattr(deps, "conversation_language", None)
    spoken = getattr(deps, "spoken_language", "en") or "en"
    payload: dict[str, object] = {
        "language": getattr(snap, "user_language", None) or spoken,
        "language_code": getattr(snap, "narration_language", None) or spoken,
        "language_confidence": float(getattr(snap, "confidence", 1.0) or 1.0),
        "current_narration": narration,
        "speech_status": status,
    }
    hook = getattr(deps, "on_speech", None)
    if callable(hook):
        hook(payload)
    if state is not None:
        state["user_language"] = payload["language"]
        state["narration_language"] = payload["language_code"]
        state["language_confidence"] = payload["language_confidence"]
    return payload


def sync_heard_language(deps: object, utterance: str, *, is_echo: bool = False) -> dict[str, object]:
    """Apply one heard line to CallDeps language tracker. Returns CallState patch."""
    live = getattr(deps, "live_agent", None)
    bot_speaking = bool(getattr(live, "speaking", False))
    phase = getattr(live, "playback_phase", "") or ""
    if phase in {"synthesizing", "buffering", "playing", "draining"}:
        bot_speaking = True
    echo = is_echo
    check = getattr(deps, "is_bot_echo", None)
    if callable(check) and utterance:
        try:
            echo = echo or bool(check(utterance))
        except Exception:  # noqa: BLE001
            pass
    current = getattr(deps, "spoken_language", "en") or "en"
    snap = getattr(deps, "conversation_language", None)
    if not isinstance(snap, ConversationLanguage):
        snap = ConversationLanguage(
            user_language=str(current),
            narration_language=str(current),
        )
    extra = getattr(deps, "extra_languages", ("hi",)) or ("hi",)
    allowed = frozenset({str(current), *[str(x) for x in extra]})
    out = observe_user_utterance(
        utterance,
        snap,
        is_echo=echo,
        bot_speaking=bot_speaking,
        allowed=allowed,
    )
    apply_to_deps(deps, out)
    return out.as_state()


def apply_to_deps(deps: object, snap: ConversationLanguage) -> None:
    setattr(deps, "conversation_language", snap)
    if snap.narration_language in TTS_LANGUAGES:
        from navigator.voice.language import SpokenLanguage, apply_to_speakers

        lang: SpokenLanguage = snap.narration_language  # type: ignore[assignment]
        setattr(deps, "spoken_language", lang)
        speaker = getattr(deps, "speaker", None)
        local = getattr(speaker, "local", None) if speaker is not None else None
        live = getattr(deps, "live_agent", None)
        apply_to_speakers(lang, speaker, local, live)
    if snap.source == "echo_ignored":
        return
    if snap.tts_fallback:
        print(
            f"[language] detected={snap.user_language} tts_fallback="
            f"{snap.narration_language} source={snap.source}",
            flush=True,
        )
        return
    print(
        f"[language] user={snap.user_language} narration={snap.narration_language} "
        f"conf={snap.confidence:.2f} source={snap.source}",
        flush=True,
    )
