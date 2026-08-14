"""When Live is up, MeetSpeaker TTS must not fire (dual-path breaks Meet audio)."""

from __future__ import annotations

import inspect


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


def test_start_live_agent_not_gated_on_flag():
    from navigator.meeting import live_demo

    src = inspect.getsource(live_demo._start_live_agent)
    assert "if not settings.live_conversational" not in src
    assert "using TTS" not in src


def test_meet_uses_print_speaker_not_wav_cascade():
    from navigator.meeting import live_demo

    assert not hasattr(live_demo, "_local_speaker_for_meet")
    assert not hasattr(live_demo, "_require_tts_for_meet")
    assert not hasattr(live_demo, "_make_live_speaker")
