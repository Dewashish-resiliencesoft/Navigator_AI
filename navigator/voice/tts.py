"""Speaker protocol + PrintSpeaker. Meet voice is Gemini Live PCM, not WAV TTS."""

from __future__ import annotations

from typing import Protocol


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
