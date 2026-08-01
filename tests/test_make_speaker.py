"""make_speaker picks Fish (main) or Piper."""

from navigator.voice.fish_tts import FishSpeaker
from navigator.voice.tts import PrintSpeaker, make_speaker


def test_make_speaker_prefers_fish_when_key_set():
    sp = make_speaker(fish_api_key="sk-test", tts_provider="auto")
    assert isinstance(sp, FishSpeaker)


def test_make_speaker_mute():
    assert isinstance(make_speaker(mute=True, fish_api_key="sk"), PrintSpeaker)


def test_make_speaker_fish_required_without_key_raises():
    import pytest

    with pytest.raises(RuntimeError, match="FISH_API_KEY"):
        make_speaker(tts_provider="fish", fish_api_key="", require_audio=True)
