"""Speaker protocol + PrintSpeaker.

Meet voice is Gemini Live PCM by default. ``NAVIGATOR_VOICE_PROVIDER=nvidia``
speaks the same phrased text through :class:`NvidiaSpeaker`, which pushes
16-bit PCM into the Attendee bridge. NVIDIA errors fall back to ``fallback``.
"""

from __future__ import annotations

from typing import Any, Protocol

from navigator.voice.nvidia import NvidiaVoiceError, synthesize_pcm, voice_selected


class Speaker(Protocol):
    """What SPEAKING depends on. Swappable without touching the state machine."""

    def say(self, text: str, *, language: str | None = None) -> None: ...


class PrintSpeaker:
    """Speaker for tests, mute, and non-Meet runs. Records what was said."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.languages: list[str | None] = []

    def say(self, text: str, *, language: str | None = None) -> None:
        self.said.append(text)
        self.languages.append(language)
        print(f"[speak] {text}")


class NvidiaSpeaker:
    """Speak already-phrased text with hosted Magpie TTS into the PCM bridge.

    ``bridge`` is anything with ``push_outbound_pcm(pcm, sample_rate=, writer=)``
    — the live Attendee :class:`AudioBridge`. On HTTP 429 or any other
    NVIDIA error, log it and call ``fallback`` (the Gemini Live mouth).
    """

    def __init__(
        self,
        bridge: Any,
        *,
        fallback: Speaker | None = None,
        language: str = "en",
    ) -> None:
        self.bridge = bridge
        self.fallback = fallback
        self.language = language
        self.said: list[str] = []
        self.languages: list[str | None] = []
        self.fell_back = False

    def set_language(self, lang: str) -> None:
        self.language = lang

    def say(self, text: str, *, language: str | None = None) -> None:
        if not (text or "").strip():
            return
        lang = language or self.language
        self.said.append(text)
        self.languages.append(lang)
        try:
            self.speak_pcm(text, language=lang or "en")
        except NvidiaVoiceError as exc:
            self.fell_back = True
            print(f"[nvidia-tts] falling back to Gemini ({exc})", flush=True)
            if self.fallback is None:
                return
            try:
                self.fallback.say(text, language=language)
            except TypeError:
                self.fallback.say(text)

    def speak_pcm(self, text: str, *, language: str) -> float:
        """Push PCM for ``text``. Returns seconds of audio. Raises on failure."""

        def _push(pcm: bytes, rate: int) -> None:
            self.bridge.push_outbound_pcm(pcm, sample_rate=rate, writer="nvidia-tts")

        return synthesize_pcm(text, language=language, on_chunk=_push)


def try_nvidia_tts(bridge: Any, text: str, *, language: str) -> float | None:
    """Speak ``text`` on NVIDIA when that provider is selected.

    Returns seconds of audio, or ``None`` when the caller should use the
    Gemini Live path (provider is not nvidia, or NVIDIA failed).
    """
    if not voice_selected():
        return None
    speaker = NvidiaSpeaker(bridge, language=language)
    try:
        return speaker.speak_pcm(text, language=language)
    except NvidiaVoiceError as exc:
        print(f"[nvidia-tts] falling back to Gemini ({exc})", flush=True)
        return None
