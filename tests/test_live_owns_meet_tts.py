"""When Live is up, MeetSpeaker TTS must not fire (dual-path breaks Meet audio)."""

from __future__ import annotations

import inspect


class _FakeLive:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str, mode: str = "natural", **_kwargs) -> None:
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


def test_meet_speaker_pass_through_verbatim_mode():
    from navigator.meeting.live_demo import _own_meet_tts_when_live

    live = _FakeLive()
    meet = _FakeMeet()
    box = [live]
    _own_meet_tts_when_live(meet, box)
    meet.say("What is your name?", mode="verbatim")
    assert live.said == ["verbatim:What is your name?"]


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
    src = inspect.getsource(live_demo)
    assert "using TTS" not in src
    assert "no TTS fallback" not in src


def test_start_live_agent_tries_next_key(monkeypatch):
    from types import SimpleNamespace

    from navigator.meeting import live_demo

    starts: list[str] = []

    class FakeAgent:
        def __init__(self, cfg, bridge):
            self.cfg = cfg
            self._failed = "1011 None. The service is currently unavailable."

        def start(self, *, timeout_s: float = 15.0) -> bool:
            starts.append(self.cfg.api_key)
            return self.cfg.api_key == "k2"

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "navigator.core.gemini_keys.gemini_live_key_candidates", lambda: ["k1", "k2"]
    )
    monkeypatch.setattr("navigator.voice.live_agent.LiveAgent", FakeAgent)
    monkeypatch.setattr(
        "navigator.voice.live_persona.build_live_instruction",
        lambda **_k: "be helpful",
    )
    monkeypatch.setattr(live_demo, "load_agent_context", lambda *_a, **_k: "")
    graph = SimpleNamespace(site="product")
    agent = live_demo._start_live_agent(
        audio_bridge=object(),
        graph_cfg=graph,
        product_id="product",
        intake=None,
        spoken_language="en",
        agent_gender="female",
    )
    assert starts == ["k1", "k2"]
    assert isinstance(agent, FakeAgent)
    assert agent.cfg.api_key == "k2"
