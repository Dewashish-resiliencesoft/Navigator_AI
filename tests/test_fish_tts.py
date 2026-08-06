"""Fish Audio TTS speaker."""

from __future__ import annotations

import io
import wave

from navigator.voice.fish_tts import DEFAULT_SARAH_ID, FREE_MODEL, FishSpeaker


def _tiny_wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 320)
    return buf.getvalue()


def test_available_requires_api_key():
    assert FishSpeaker("").available() is False
    assert FishSpeaker("  ").available() is False
    assert FishSpeaker("sk-test").available() is True


def test_synthesize_mp3_posts_sarah_free_model():
    seen: dict = {}

    def fake_post(url, *, headers, body):
        seen["url"] = url
        seen["headers"] = headers
        seen["body"] = body
        return b"ID3" + b"\x00" * 32

    sp = FishSpeaker("sk-test", post=fake_post)
    mp3 = sp.synthesize_mp3("Hello from the demo.")
    assert mp3 is not None
    assert mp3[:3] == b"ID3"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert seen["headers"]["model"] == FREE_MODEL
    assert seen["body"]["reference_id"] == DEFAULT_SARAH_ID
    assert seen["body"]["format"] == "mp3"
    assert seen["body"]["mp3_bitrate"] == 128
    assert seen["body"]["text"] == "Hello from the demo."


def test_synthesize_wav_posts_sarah_free_model():
    seen: dict = {}

    def fake_post(url, *, headers, body):
        seen["url"] = url
        seen["headers"] = headers
        seen["body"] = body
        return _tiny_wav()

    sp = FishSpeaker("sk-test", post=fake_post)
    wav = sp.synthesize_wav("Hello from ResilioHub.")
    assert wav is not None
    assert wav[:4] == b"RIFF"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert seen["headers"]["model"] == FREE_MODEL
    assert seen["body"]["reference_id"] == DEFAULT_SARAH_ID
    assert seen["body"]["format"] == "wav"
    assert seen["body"]["text"] == "Hello from ResilioHub."


def test_synthesize_wav_empty_text():
    sp = FishSpeaker("sk-test", post=lambda *a, **k: _tiny_wav())
    assert sp.synthesize_wav("   ") is None


def test_synthesize_wav_rejects_non_wav():
    sp = FishSpeaker("sk-test", post=lambda *a, **k: b"ID3fake-mp3")
    assert sp.synthesize_wav("hi") is None


def test_synthesize_wav_propagates_http_error_as_none(capsys):
    def boom(*a, **k):
        raise RuntimeError("HTTP 401: no")

    sp = FishSpeaker("sk-bad", post=boom)
    assert sp.synthesize_wav("hi") is None
    err = capsys.readouterr().out
    assert "fish tts failed" in err
