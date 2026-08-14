"""MeetSpeaker is glue only — no WAV upload to Attendee."""

from __future__ import annotations

import inspect

from navigator.meeting.meet_speaker import MeetSpeaker
from navigator.voice.tts import PrintSpeaker


class _FakeAttendee:
    def __init__(self) -> None:
        self.spoken: list[bytes] = []
        self.chats: list[str] = []

    def speak(self, bot_id: str, wav: bytes) -> None:
        self.spoken.append(wav)

    def send_chat(self, bot_id: str, text: str) -> None:
        self.chats.append(text)


def test_meet_speaker_has_no_wav_synth():
    sig = inspect.signature(MeetSpeaker.__init__)
    assert "synthesizer" not in sig.parameters
    att = _FakeAttendee()
    ms = MeetSpeaker(PrintSpeaker(), att, "bot")
    assert not hasattr(ms, "prefetch_lines")
    assert not hasattr(ms, "synthesizer")
    ms.say("Hello from Meet.")
    assert att.spoken == []
    assert ms.last_spoken == "Hello from Meet."
