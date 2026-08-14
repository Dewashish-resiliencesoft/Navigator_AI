"""Speaker protocol + PrintSpeaker. Meet voice is Gemini Live PCM, not WAV TTS."""

from __future__ import annotations

from typing import Protocol


class Speaker(Protocol):
    """What SPEAKING depends on. Swappable without touching the state machine."""

    def say(self, text: str) -> None: ...


class PrintSpeaker:
    """Speaker for tests, mute, and non-Meet runs. Records what was said."""

    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)
        print(f"[speak] {text}")
