"""When Live is up, MeetSpeaker TTS must not fire (dual-path breaks Meet audio)."""

from __future__ import annotations


class _FakeLive:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str, mode: str = "natural") -> None:
        self.said.append(f"{mode}:{text}")


class _FakeMeet:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)


def test_meet_speaker_routes_to_live_when_owned():
    from navigator.meeting.live_demo import _own_meet_tts_when_live

    live = _FakeLive()
    meet = _FakeMeet()
    box = [live]
    _own_meet_tts_when_live(meet, box)
    meet.say("Starting the demo now.")
    assert live.said == ["natural:Starting the demo now."]
    assert meet.said == []
