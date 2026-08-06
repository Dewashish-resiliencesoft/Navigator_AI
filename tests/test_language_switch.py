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
