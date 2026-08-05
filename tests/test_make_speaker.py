"""make_speaker picks Gemini Live (main), Fish, or Piper."""

import pytest

from navigator.voice.fish_tts import FishSpeaker
from navigator.voice.gemini_live import GeminiLiveSpeaker
from navigator.voice.tts import PrintSpeaker, make_speaker


def test_make_speaker_prefers_gemini_when_key_set():
    sp = make_speaker(gemini_api_key="gem-test", tts_provider="auto")
    assert isinstance(sp, GeminiLiveSpeaker)


def test_make_speaker_prefers_fish_when_only_fish_key():
    sp = make_speaker(fish_api_key="sk-test", tts_provider="auto")
    assert isinstance(sp, FishSpeaker)


def test_make_speaker_mute():
    assert isinstance(make_speaker(mute=True, gemini_api_key="gk"), PrintSpeaker)


def test_make_speaker_gemini_required_without_key_raises():
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        make_speaker(tts_provider="gemini", gemini_api_key="", require_audio=True)


def test_make_speaker_fish_required_without_key_raises():
    with pytest.raises(RuntimeError, match="FISH_API_KEY"):
        make_speaker(tts_provider="fish", fish_api_key="", require_audio=True)
