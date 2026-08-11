"""Language switch detection for live demos."""

from __future__ import annotations

import pytest

from navigator.voice.language import (
    detect_language_switch,
    is_language_switch_only,
    sync_speaker_language,
)


def test_detect_hindi_switch():
    assert detect_language_switch("Can we talk in Hindi?") == "hi"
    assert detect_language_switch("Hindi mein baat karo") == "hi"
    assert detect_language_switch("hindi me baat karni hai") == "hi"
    assert detect_language_switch("talk to me in hindi") == "hi"
    assert detect_language_switch("Please speak to me in Hindi") == "hi"
    assert detect_language_switch("say this in hindi") == "hi"
    assert detect_language_switch("say it in Hindi please") == "hi"
    assert detect_language_switch("Hey navigator, could you speak me with Hindi?") == "hi"


def test_sync_call_language_notifies_live_agent():
    from navigator.voice.language import sync_call_language

    class Live:
        def __init__(self) -> None:
            self.lang = "en"

        def set_language(self, lang: str) -> None:
            self.lang = lang

    class Deps:
        spoken_language = "en"
        extra_languages = ("hi",)
        speaker = None
        live_agent = Live()

    deps = Deps()
    assert sync_call_language(deps, "talk in hindi") == "hi"
    assert deps.spoken_language == "hi"
    assert deps.live_agent.lang == "hi"


def test_detect_english_switch():
    assert detect_language_switch("Switch back to English please") == "en"
    assert detect_language_switch("in english please") == "en"


def test_no_switch_on_normal_question():
    assert detect_language_switch("Show me the dashboard") is None


def test_switch_only_vs_combined():
    assert is_language_switch_only("Hindi mein baat karo") is True
    assert is_language_switch_only("Hindi mein baat karo, dashboard dikhao") is False


class _FakeSpeaker:
    def __init__(self) -> None:
        self.spoken_language = "en"

    def set_language(self, lang: str) -> None:
        self.spoken_language = lang


def test_sync_speaker_language():
    sp = _FakeSpeaker()
    out = sync_speaker_language(sp, "speak hindi", current="en")
    assert out == "hi"
    assert sp.spoken_language == "hi"


def test_poll_barge_in_language_switch():
    from navigator.voice.language import poll_barge_in_language_switch

    class Deps:
        spoken_language = "en"
        extra_languages = ("hi",)
        pending_barge_in = ["Hindi mein baat karo"]
        speaker = _FakeSpeaker()

    deps = Deps()
    assert poll_barge_in_language_switch(deps) == "hi"
    assert deps.spoken_language == "hi"
    assert deps.speaker.spoken_language == "hi"
