"""WAV TTS stack is gone. Mouth is Live PCM or PrintSpeaker."""

from __future__ import annotations

import pytest

from navigator.core.settings import Settings
from navigator.voice.tts import PrintSpeaker


def test_print_speaker_records():
    sp = PrintSpeaker()
    sp.say("hi")
    assert sp.said == ["hi"]


def test_wav_tts_stack_gone():
    import navigator.voice.tts as tts

    assert not hasattr(tts, "make_speaker")
    assert not hasattr(tts, "CascadeSpeaker")
    assert not hasattr(tts, "PiperSpeaker")
    with pytest.raises(ModuleNotFoundError):
        import navigator.voice.fish_tts  # noqa: F401
    with pytest.raises(ModuleNotFoundError):
        import navigator.voice.gemini_live  # noqa: F401


def test_settings_drop_tts_and_live_flag():
    fields = Settings.model_fields
    assert "live_conversational_model" in fields
    assert "gemini_live_voice" in fields
    for gone in (
        "live_conversational",
        "tts_provider",
        "fish_api_key",
        "fish_model",
        "fish_reference_id",
        "piper_voice",
        "piper_data_dir",
        "gemini_live_model",
    ):
        assert gone not in fields
