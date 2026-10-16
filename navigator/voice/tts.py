"""Speaker protocol + PrintSpeaker.

Meet voice is Gemini Live PCM by default. ``NAVIGATOR_VOICE_PROVIDER=nvidia``
speaks the same phrased text through :class:`NvidiaSpeaker`.
``NAVIGATOR_VOICE_PROVIDER=fish`` uses :class:`FishSpeaker` (Fish Audio
``s2.1-pro-free``, streaming PCM). Both push 16-bit PCM into the Attendee
bridge. Hosted errors fall back to ``fallback`` (Gemini Live).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from navigator.voice.nvidia import (
    NvidiaVoiceError,
    synthesize_pcm,
    voice_selected as nvidia_voice_selected,
)


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

    def speak_pcm(
        self,
        text: str,
        *,
        language: str,
        cancel: Callable[[], bool] | None = None,
        on_first_audio: Callable[[], None] | None = None,
    ) -> float:
        """Push PCM for ``text``. Returns seconds of audio. Raises on failure."""
        first = {"done": False}

        def _push(pcm: bytes, rate: int) -> None:
            if not first["done"]:
                first["done"] = True
                if on_first_audio is not None:
                    try:
                        on_first_audio()
                    except Exception as exc:  # noqa: BLE001
                        print(f"[nvidia-tts] ttfa hook failed: {exc}", flush=True)
            self.bridge.push_outbound_pcm(pcm, sample_rate=rate, writer="nvidia-tts")

        return synthesize_pcm(text, language=language, on_chunk=_push, cancel=cancel)


def try_nvidia_tts(
    bridge: Any,
    text: str,
    *,
    language: str,
    cancel: Callable[[], bool] | None = None,
    on_first_audio: Callable[[], None] | None = None,
) -> float | None:
    """Speak ``text`` on NVIDIA when that provider is selected.

    Returns seconds of audio, or ``None`` when the caller should use the
    Gemini Live path (provider is not nvidia, or NVIDIA failed). A barge-in
    cancel returns the seconds already pushed (possibly 0) so the line is
    not spoken a second time through Gemini.
    """
    if not nvidia_voice_selected():
        return None
    speaker = NvidiaSpeaker(bridge, language=language)
    try:
        return speaker.speak_pcm(
            text, language=language, cancel=cancel, on_first_audio=on_first_audio
        )
    except NvidiaVoiceError as exc:
        print(f"[nvidia-tts] falling back to Gemini ({exc})", flush=True)
        return None


class FishSpeaker:
    """Speak already-phrased text with Fish Audio streaming TTS.

    Same bridge contract as :class:`NvidiaSpeaker`. The free model header
    is always ``s2.1-pro-free``. HTTP errors fall back to ``fallback``.
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
        from navigator.voice.fish_audio import FishAudioError

        self.said.append(text)
        self.languages.append(lang)
        try:
            self.speak_pcm(text, language=lang or "en")
        except FishAudioError as exc:
            self.fell_back = True
            print(f"[fish-tts] falling back to Gemini ({exc})", flush=True)
            if self.fallback is None:
                return
            try:
                self.fallback.say(text, language=language)
            except TypeError:
                self.fallback.say(text)

    def speak_pcm(
        self,
        text: str,
        *,
        language: str,
        cancel: Callable[[], bool] | None = None,
        on_first_audio: Callable[[], None] | None = None,
    ) -> float:
        """Push PCM for ``text``. Returns seconds of audio. Raises on failure."""
        from navigator.voice.fish_audio import synthesize_pcm as fish_pcm

        first = {"done": False}

        def _push(pcm: bytes, rate: int) -> None:
            if not first["done"]:
                first["done"] = True
                if on_first_audio is not None:
                    try:
                        on_first_audio()
                    except Exception as exc:  # noqa: BLE001
                        print(f"[fish-tts] ttfa hook failed: {exc}", flush=True)
            self.bridge.push_outbound_pcm(pcm, sample_rate=rate, writer="fish-tts")

        return fish_pcm(text, language=language, on_chunk=_push, cancel=cancel)


def try_fish_tts(
    bridge: Any,
    text: str,
    *,
    language: str,
    cancel: Callable[[], bool] | None = None,
    on_first_audio: Callable[[], None] | None = None,
) -> float | None:
    """Speak ``text`` on Fish Audio when that provider is selected.

    Returns seconds of audio, or ``None`` when the caller should use
    Gemini Live. A barge-in returns the seconds already pushed (possibly
    0) so the line is not spoken again through Gemini.
    """
    from navigator.voice.fish_audio import FishAudioError, voice_selected as fish_selected

    if not fish_selected():
        return None
    speaker = FishSpeaker(bridge, language=language)
    try:
        return speaker.speak_pcm(
            text, language=language, cancel=cancel, on_first_audio=on_first_audio
        )
    except FishAudioError as exc:
        print(f"[fish-tts] falling back to Gemini ({exc})", flush=True)
        return None


def try_hosted_tts(
    bridge: Any,
    text: str,
    *,
    language: str,
    cancel: Callable[[], bool] | None = None,
    on_first_audio: Callable[[], None] | None = None,
) -> float | None:
    """Route a phrased line to the selected hosted mouth.

    ``gemini_live`` returns ``None`` immediately (no HTTP). NVIDIA and
    Fish return seconds of audio, or ``None`` when Gemini should speak.
    """
    from navigator.core.settings import settings

    provider = settings.voice_provider
    if provider == "nvidia":
        return try_nvidia_tts(
            bridge,
            text,
            language=language,
            cancel=cancel,
            on_first_audio=on_first_audio,
        )
    if provider == "fish":
        return try_fish_tts(
            bridge,
            text,
            language=language,
            cancel=cancel,
            on_first_audio=on_first_audio,
        )
    return None
