"""Speaker that also pushes audio into a Meet/Zoom bot via Attendee."""

from __future__ import annotations

from typing import Protocol

from navigator.meeting.attendee import AttendeeClient
from navigator.voice.tts import Speaker


class WavSynthesizer(Protocol):
    def synthesize_wav(self, text: str) -> bytes | None: ...


class MeetSpeaker:
    """Local TTS + Attendee output_audio. Falls back to Meet chat if speak fails."""

    def __init__(
        self,
        local: Speaker,
        attendee: AttendeeClient,
        bot_id: str,
        *,
        synthesizer: WavSynthesizer | None = None,
        also_chat: bool = False,
    ) -> None:
        self.local = local
        self.attendee = attendee
        self.bot_id = bot_id
        self.synthesizer = synthesizer
        self.also_chat = also_chat

    def say(self, text: str) -> None:
        self.local.say(text)
        if self.also_chat and text.strip():
            try:
                self.attendee.send_chat(self.bot_id, text)
            except Exception as exc:  # noqa: BLE001
                print(f"[speak] Meet chat failed: {exc}", flush=True)
        synth = self.synthesizer
        if synth is None and hasattr(self.local, "synthesize_wav"):
            synth = self.local  # type: ignore[assignment]
        if synth is None or not text.strip():
            return
        try:
            wav = synth.synthesize_wav(text)
            if wav:
                self.attendee.speak(self.bot_id, wav)
        except Exception as exc:  # noqa: BLE001
            print(f"[speak] Meet audio failed: {exc}", flush=True)
