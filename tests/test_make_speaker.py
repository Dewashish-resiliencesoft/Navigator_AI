"""make_speaker picks Gemini Live (main), Fish, or Piper."""

import pytest

from navigator.voice.fish_tts import FishSpeaker
from navigator.voice.gemini_live import GeminiLiveSpeaker
from navigator.voice.tts import CascadeSpeaker, PrintSpeaker, make_speaker


def test_make_speaker_prefers_gemini_when_key_set(monkeypatch):
    monkeypatch.setattr(
        "navigator.voice.tts.PiperSpeaker.available",
        lambda self: False,
    )
    sp = make_speaker(gemini_api_key="gem-test", tts_provider="auto")
    assert isinstance(sp, GeminiLiveSpeaker)


def test_make_speaker_cascades_gemini_then_fish(monkeypatch):
    monkeypatch.setattr(
        "navigator.voice.tts.PiperSpeaker.available",
        lambda self: False,
    )
    sp = make_speaker(
        gemini_api_key="gem-test",
        fish_api_key="sk-test",
        tts_provider="auto",
    )
    assert isinstance(sp, CascadeSpeaker)
    assert isinstance(sp._speakers[0], GeminiLiveSpeaker)
    assert isinstance(sp._speakers[1], FishSpeaker)


def test_cascade_speaker_falls_through_on_empty():
    primary = type(
        "P",
        (),
        {
            "available": lambda self: True,
            "synthesize_wav": lambda self, t: None,
        },
    )()
    secondary = type(
        "S",
        (),
        {
            "available": lambda self: True,
            "synthesize_wav": lambda self, t: b"RIFF",
        },
    )()
    sp = CascadeSpeaker([primary, secondary])
    assert sp.synthesize_wav("hi") == b"RIFF"
    assert sp._active == 1


def test_make_speaker_prefers_fish_when_only_fish_key(monkeypatch):
    monkeypatch.setattr(
        "navigator.voice.tts.PiperSpeaker.available",
        lambda self: False,
    )
    sp = make_speaker(fish_api_key="sk-test", tts_provider="auto")
    assert isinstance(sp, FishSpeaker)


def test_make_speaker_mute():
    assert isinstance(make_speaker(mute=True, gemini_api_key="gk"), PrintSpeaker)


def test_make_speaker_gemini_required_without_key_raises(monkeypatch):
    monkeypatch.setattr(
        "navigator.core.gemini_keys.gemini_key_candidates",
        lambda: [],
    )
    monkeypatch.setattr(
        "navigator.voice.tts.PiperSpeaker.available",
        lambda self: False,
    )
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        make_speaker(tts_provider="gemini", gemini_api_key="", require_audio=True)


def test_make_speaker_fish_required_without_key_raises(monkeypatch):
    monkeypatch.setattr(
        "navigator.voice.tts.PiperSpeaker.available",
        lambda self: False,
    )
    with pytest.raises(RuntimeError, match="FISH_API_KEY"):
        make_speaker(tts_provider="fish", fish_api_key="", require_audio=True)
