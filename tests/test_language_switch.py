"""Language switch detection for live demos."""

from __future__ import annotations

import pytest

from navigator.voice.language import (
    detect_language_switch,
    detect_spoken_language,
    is_language_switch_only,
    language_code,
    sync_speaker_language,
)


def test_english_uses_indian_accent_code():
    assert language_code("en") == "en-IN"
    assert language_code("hi") == "hi-IN"


def test_detect_spoken_language_from_script():
    # Speaking Hindi (Devanagari) with no "hindi" keyword must still register as hi.
    assert detect_spoken_language("मुझे दिखाओ यह कैसे काम करता है") == "hi"
    assert detect_spoken_language("क्या तुम मदद कर सकती हो?") == "hi"
    # A real English sentence → en.
    assert detect_spoken_language("show me how the dashboard works") == "en"
    # Short/ambiguous backchannel → None so it doesn't thrash mid-demo.
    assert detect_spoken_language("haan") is None
    assert detect_spoken_language("") is None


def test_speaking_hindi_switches_without_keyword():
    # apply_language_switch must flip on script alone (the reported bug).
    from navigator.voice.language import apply_language_switch

    seen: list[str] = []
    new_lang, ack = apply_language_switch(
        utterance="मुझे यह फीचर दिखाओ",
        current="en",
        on_switch=lambda lang: seen.append(lang),
        allowed=frozenset({"en", "hi"}),
    )
    assert new_lang == "hi"
    assert seen == ["hi"]
    # Natural speech, not an explicit "switch" request → no ack line.
    assert ack is None


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
