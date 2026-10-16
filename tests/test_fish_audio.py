"""Fish Audio streaming TTS. HTTP is mocked. Default mouth stays Gemini Live."""

from __future__ import annotations

import asyncio
import io
import json
from email.message import Message
from urllib.error import HTTPError

import pytest

from navigator.agent_runtime.models import AgentEventKind
from navigator.agent_runtime.sync_clock import sync_events
from navigator.core.settings import Settings, settings
from navigator.voice.fish_audio import FishAudioError, synthesize_pcm
from navigator.voice.live_agent import LiveAgent, LiveAgentConfig, _Cmd
from navigator.voice.tts import FishSpeaker, PrintSpeaker, try_fish_tts, try_hosted_tts


def test_fish_settings_keep_gemini_default_and_forbidden_names():
    fields = Settings.model_fields
    assert fields["voice_provider"].default == "gemini_live"
    assert set(fields["voice_provider"].annotation.__args__) == {
        "gemini_live",
        "nvidia",
        "fish",
    }
    assert fields["fish_audio_api_key"].default == ""
    assert fields["fish_audio_model"].default == "s2.1-pro-free"
    assert fields["fish_audio_sample_rate"].default == 24_000
    for gone in ("tts_provider", "fish_api_key", "fish_model", "fish_reference_id"):
        assert gone not in fields
    with pytest.raises(ModuleNotFoundError):
        import navigator.voice.fish_tts  # noqa: F401


class _Bridge:
    def __init__(self) -> None:
        self.chunks: list[tuple[bytes, int, str | None]] = []
        self.ended = False

    def push_outbound_pcm(self, pcm: bytes, *, sample_rate: int = 16000, writer: str | None = None) -> None:
        self.chunks.append((pcm, sample_rate, writer))

    def mark_line_end(self) -> None:
        self.ended = True


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            out, self._data = self._data, b""
            return out
        out, self._data = self._data[:n], self._data[n:]
        return out

    def close(self) -> None:
        return None


def _arm(monkeypatch, body: bytes | None = None, *, status: int | None = None) -> dict:
    captured: dict = {"calls": 0}

    def fake_urlopen(req, timeout=0):
        captured["calls"] += 1
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["model"] = req.get_header("Model")
        captured["content_type"] = req.get_header("Content-type")
        captured["body"] = json.loads(req.data.decode())
        if status is not None:
            raise HTTPError(
                req.full_url,
                status,
                "nope",
                Message(),
                io.BytesIO(b'{"detail":"rate limit"}'),
            )
        return _Body(body if body is not None else b"\x01\x00" * 24)

    monkeypatch.setattr(settings, "voice_provider", "fish")
    monkeypatch.setattr(settings, "fish_audio_api_key", "test-fish-key")
    monkeypatch.setattr(settings, "fish_audio_base_url", "https://api.fish.audio/v1/tts")
    monkeypatch.setattr(settings, "fish_audio_sample_rate", 24000)
    monkeypatch.setattr(settings, "fish_audio_model", "s2.1-pro-free")
    monkeypatch.setattr("navigator.voice.fish_audio._urlopen", fake_urlopen)
    return captured


def test_fish_speaker_streams_pcm_with_free_model_header(monkeypatch):
    captured = _arm(monkeypatch, b"\x02\x00" * 50)
    bridge = _Bridge()
    FishSpeaker(bridge, language="en").say("Hello there")
    assert captured["calls"] == 1
    assert captured["url"] == "https://api.fish.audio/v1/tts"
    assert captured["auth"] == "Bearer test-fish-key"
    assert captured["model"] == "s2.1-pro-free"
    assert captured["content_type"] == "application/json"
    assert captured["body"] == {
        "text": "Hello there",
        "format": "pcm",
        "sample_rate": 24000,
        "latency": "balanced",
    }
    assert "reference_id" not in captured["body"]
    pcm, rate, writer = bridge.chunks[0]
    assert pcm[:2] == b"\x02\x00"
    assert rate == 24000
    assert writer == "fish-tts"


def test_blank_model_still_sends_free_header(monkeypatch):
    captured = _arm(monkeypatch, b"\x01\x00" * 8)
    monkeypatch.setattr(settings, "fish_audio_model", "")
    synthesize_pcm("Hi", language="en", on_chunk=lambda pcm, rate: None)
    assert captured["model"] == "s2.1-pro-free"
    assert captured["calls"] == 1


def test_paid_model_setting_is_ignored(monkeypatch, capsys):
    captured = _arm(monkeypatch, b"\x01\x00" * 8)
    monkeypatch.setattr(settings, "fish_audio_model", "s2.1-pro")
    synthesize_pcm("Hi", language="hi", on_chunk=lambda pcm, rate: None)
    assert captured["model"] == "s2.1-pro-free"
    assert "ignoring model" in capsys.readouterr().out


def test_fish_429_falls_back_once(monkeypatch, capsys):
    captured = _arm(monkeypatch, status=429)
    fallback = PrintSpeaker()
    speaker = FishSpeaker(_Bridge(), fallback=fallback)
    speaker.say("still say this")
    assert captured["calls"] == 1
    assert speaker.fell_back is True
    assert fallback.said == ["still say this"]
    assert "falling back to Gemini" in capsys.readouterr().out


def test_missing_fish_key_does_not_call_http(monkeypatch):
    called = {"n": 0}

    def boom(req, timeout=0):
        called["n"] += 1
        raise AssertionError("http")

    monkeypatch.setattr(settings, "voice_provider", "fish")
    monkeypatch.setattr(settings, "fish_audio_api_key", "")
    monkeypatch.setattr("navigator.voice.fish_audio._urlopen", boom)
    fallback = PrintSpeaker()
    FishSpeaker(_Bridge(), fallback=fallback).say("hello")
    assert called["n"] == 0
    assert fallback.said == ["hello"]


def test_try_fish_skipped_unless_selected(monkeypatch):
    monkeypatch.setattr(settings, "voice_provider", "gemini_live")
    called = {"n": 0}
    monkeypatch.setattr(
        "navigator.voice.fish_audio._urlopen",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1),
    )
    assert try_fish_tts(_Bridge(), "hi", language="en") is None
    assert try_hosted_tts(_Bridge(), "hi", language="en") is None
    assert called["n"] == 0


def test_barge_in_returns_audio_already_pushed(monkeypatch):
    chunks = [b"\x03\x00" * 20, b"\x04\x00" * 20]

    class _Pieces:
        def __init__(self) -> None:
            self._left = list(chunks)

        def read(self, n: int = -1) -> bytes:
            if not self._left:
                return b""
            return self._left.pop(0)

        def close(self) -> None:
            return None

    monkeypatch.setattr(settings, "voice_provider", "fish")
    monkeypatch.setattr(settings, "fish_audio_api_key", "test-fish-key")
    monkeypatch.setattr("navigator.voice.fish_audio._urlopen", lambda req, timeout=0: _Pieces())
    seen: list[bytes] = []
    stop = {"on": False}

    def _push(pcm: bytes, rate: int) -> None:
        seen.append(pcm)
        stop["on"] = True

    seconds = synthesize_pcm(
        "Keep going please",
        language="en",
        on_chunk=_push,
        cancel=lambda: stop["on"],
    )
    assert seconds > 0
    assert len(seen) == 1


def test_cancel_before_http_returns_zero(monkeypatch):
    called = {"n": 0}

    def boom(req, timeout=0):
        called["n"] += 1
        raise AssertionError("http")

    monkeypatch.setattr(settings, "fish_audio_api_key", "test-fish-key")
    monkeypatch.setattr("navigator.voice.fish_audio._urlopen", boom)
    assert synthesize_pcm("Hi", language="en", on_chunk=lambda *a: None, cancel=lambda: True) == 0.0
    assert called["n"] == 0


def test_fish_error_status():
    err = FishAudioError("HTTP 429: busy", status=429)
    assert err.status == 429


async def test_pump_cmd_fish_does_not_send_text_to_gemini(monkeypatch):
    captured = _arm(monkeypatch, b"\x05\x00" * 30)
    monkeypatch.setattr(settings, "fish_audio_model", "")
    bridge = _Bridge()
    agent = LiveAgent(LiveAgentConfig(api_key="x", system_instruction="s"), bridge)
    sent: list[str] = []
    before = len(sync_events.history())

    class Session:
        async def send_realtime_input(self, text: str) -> None:
            sent.append(text)
            agent._stop.set()

    async def _run() -> None:
        agent._cmds.put(_Cmd(kind="say", text="Phrased by the brain"))
        task = asyncio.create_task(agent._pump_cmd(Session()))
        for _ in range(50):
            if bridge.ended:
                break
            await asyncio.sleep(0.02)
        agent._stop.set()
        await asyncio.wait_for(task, timeout=2)

    try:
        await _run()
    finally:
        agent._loop.close()
    assert sent == []
    assert captured["model"] == "s2.1-pro-free"
    assert bridge.chunks[0][2] == "fish-tts"
    assert agent._turn_said[-1] == "Phrased by the brain"
    chunks = [
        event
        for event in sync_events.history()[before:]
        if event.event == AgentEventKind.NARRATION_CHUNK
    ]
    assert len(chunks) == 1
    assert chunks[0].payload["text"] == "Phrased by the brain"
    assert chunks[0].payload["provider"] == "fish"
    assert chunks[0].monotonic_ns == chunks[0].payload["monotonic_ns"]
    assert chunks[0].monotonic_ns > 0
