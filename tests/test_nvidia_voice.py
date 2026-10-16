"""NVIDIA voice/STT switch. HTTP is mocked; defaults stay on Gemini and Groq."""

from __future__ import annotations

import asyncio
import io
import json
import wave
from email.message import Message
from urllib.error import HTTPError

from navigator.core.settings import Settings, settings
from navigator.voice.live_agent import LiveAgent, LiveAgentConfig, _Cmd
from navigator.voice.nvidia import NvidiaVoiceError, resolve_voice
from navigator.voice.tts import NvidiaSpeaker, PrintSpeaker, try_nvidia_tts


def test_provider_defaults_match_current_behaviour():
    fields = Settings.model_fields
    assert fields["voice_provider"].default == "gemini_live"
    assert fields["stt_provider"].default == "groq"
    assert fields["nvidia_api_key"].default == ""
    assert fields["nvidia_tts_model"].default == "magpie-tts-multilingual"
    assert fields["nvidia_stt_model"].default == "parakeet-ctc-1.1b-asr"
    assert "tts_provider" not in fields
    assert "fish_api_key" not in fields


def test_resolve_voice_en_hi_and_zeroshot_default():
    bcp, voice = resolve_voice(
        "en",
        model="magpie-tts-multilingual",
        voice="Magpie-Multilingual.EN-US.Aria",
        voice_hi="Magpie-Multilingual.HI-IN.Sofia",
    )
    assert (bcp, voice) == ("en-US", "Magpie-Multilingual.EN-US.Aria")
    bcp, voice = resolve_voice(
        "hi",
        model="magpie-tts-multilingual",
        voice="Magpie-Multilingual.EN-US.Aria",
        voice_hi="Magpie-Multilingual.HI-IN.Sofia",
    )
    assert (bcp, voice) == ("hi-IN", "Magpie-Multilingual.HI-IN.Sofia")
    _, voice = resolve_voice(
        "en",
        model="magpie-tts-zeroshot",
        voice="Magpie-Multilingual.EN-US.Aria",
        voice_hi="Magpie-Multilingual.HI-IN.Sofia",
    )
    assert voice == "Magpie-ZeroShot-Multilingual.Female"


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


def _field(body: bytes, name: str) -> str:
    marker = f'name="{name}"\r\n\r\n'.encode()
    i = body.find(marker)
    assert i >= 0, name
    return body[i + len(marker) :].split(b"\r\n", 1)[0].decode()


def _wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _arm_nvidia(monkeypatch, body: bytes | None = None, *, status: int | None = None) -> dict:
    captured: dict = {"calls": 0}

    def fake_urlopen(req, timeout=0):
        captured["calls"] += 1
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = req.data
        captured["timeout"] = timeout
        if status is not None:
            raise HTTPError(
                req.full_url,
                status,
                "nope",
                Message(),
                io.BytesIO(b'{"detail":"rate limit"}'),
            )
        return _Body(body if body is not None else b"\x01\x00" * 24)

    monkeypatch.setattr(settings, "voice_provider", "nvidia")
    monkeypatch.setattr(settings, "nvidia_api_key", "test-nvidia-key")
    monkeypatch.setattr(settings, "nvidia_tts_base_url", "https://tts.example.test")
    monkeypatch.setattr(settings, "nvidia_tts_sample_rate", 24000)
    monkeypatch.setattr("navigator.voice.nvidia._urlopen", fake_urlopen)
    return captured


def test_nvidia_speaker_posts_magpie_and_pushes_pcm(monkeypatch):
    captured = _arm_nvidia(monkeypatch, b"\x02\x00" * 50)
    bridge = _Bridge()
    speaker = NvidiaSpeaker(bridge, language="en")
    speaker.say("Hello there")
    assert speaker.said == ["Hello there"]
    assert speaker.fell_back is False
    assert captured["url"].endswith("/v1/audio/synthesize_online")
    assert captured["auth"] == "Bearer test-nvidia-key"
    assert _field(captured["body"], "text") == "Hello there"
    assert _field(captured["body"], "language") == "en-US"
    assert _field(captured["body"], "voice") == "Magpie-Multilingual.EN-US.Aria"
    assert _field(captured["body"], "encoding") == "LINEAR_PCM"
    assert _field(captured["body"], "sample_rate_hz") == "24000"
    assert bridge.chunks
    pcm, rate, writer = bridge.chunks[0]
    assert pcm[:2] == b"\x02\x00"
    assert rate == 24000
    assert writer == "nvidia-tts"


def test_nvidia_speaker_strips_wav_and_uses_header_rate(monkeypatch):
    pcm = b"\x03\x00" * 30
    captured = _arm_nvidia(monkeypatch, _wav(pcm, 22050))
    bridge = _Bridge()
    NvidiaSpeaker(bridge).say("wav please", language="hi")
    got, rate, _writer = bridge.chunks[0]
    assert got == pcm
    assert rate == 22050
    assert _field(captured["body"], "language") == "hi-IN"
    assert _field(captured["body"], "voice") == "Magpie-Multilingual.HI-IN.Sofia"


def test_nvidia_429_falls_back_to_gemini_speaker(monkeypatch, capsys):
    _arm_nvidia(monkeypatch, status=429)
    bridge = _Bridge()
    fallback = PrintSpeaker()
    speaker = NvidiaSpeaker(bridge, fallback=fallback)
    speaker.say("still say this")
    out = capsys.readouterr().out
    assert speaker.fell_back is True
    assert bridge.chunks == []
    assert fallback.said == ["still say this"]
    assert "429" in out
    assert "falling back to Gemini" in out


def test_nvidia_network_error_falls_back(monkeypatch):
    def boom(req, timeout=0):
        from urllib.error import URLError

        raise URLError("offline")

    monkeypatch.setattr(settings, "voice_provider", "nvidia")
    monkeypatch.setattr(settings, "nvidia_api_key", "test-nvidia-key")
    monkeypatch.setattr("navigator.voice.nvidia._urlopen", boom)
    fallback = PrintSpeaker()
    speaker = NvidiaSpeaker(_Bridge(), fallback=fallback)
    speaker.say("hello")
    assert speaker.fell_back is True
    assert fallback.said == ["hello"]


def test_missing_nvidia_key_does_not_call_http(monkeypatch):
    called = {"n": 0}

    def boom(req, timeout=0):
        called["n"] += 1
        raise AssertionError("http")

    monkeypatch.setattr(settings, "nvidia_api_key", "")
    monkeypatch.setattr("navigator.voice.nvidia._urlopen", boom)
    fallback = PrintSpeaker()
    NvidiaSpeaker(_Bridge(), fallback=fallback).say("hello")
    assert called["n"] == 0
    assert fallback.said == ["hello"]


def test_try_nvidia_tts_skipped_unless_provider_selected(monkeypatch):
    monkeypatch.setattr(settings, "voice_provider", "gemini_live")
    called = {"n": 0}
    monkeypatch.setattr(
        "navigator.voice.nvidia._urlopen",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1),
    )
    assert try_nvidia_tts(_Bridge(), "hi", language="en") is None
    assert called["n"] == 0


def test_transcribe_nvidia_posts_wav_and_reads_text(monkeypatch):
    captured: dict = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = req.data
        return _Body(json.dumps({"text": "hello from nvidia"}).encode())

    monkeypatch.setattr(settings, "stt_provider", "nvidia")
    monkeypatch.setattr(settings, "nvidia_api_key", "test-nvidia-key")
    monkeypatch.setattr(settings, "nvidia_stt_base_url", "https://stt.example.test")
    monkeypatch.setattr("navigator.voice.nvidia._urlopen", fake_urlopen)

    from navigator.voice.stt import transcribe

    text = transcribe(b"\x00\x01" * 20, "gsk_should_not_matter", language="en")
    assert text == "hello from nvidia"
    assert captured["url"].endswith("/v1/audio/transcriptions")
    assert b"filename=\"utterance.wav\"" in captured["body"]
    assert b"RIFF" in captured["body"]
    assert _field(captured["body"], "language") == "en-US"


def test_transcribe_nvidia_429_falls_back_to_groq(monkeypatch, capsys):
    def fake_urlopen(req, timeout=0):
        raise HTTPError(req.full_url, 429, "slow", Message(), io.BytesIO(b"busy"))

    groq_calls: dict = {}

    def fake_groq(api_key=None, **kwargs):
        groq_calls["api_key"] = api_key
        groq_calls["file"] = kwargs["file"]

        class R:
            text = "groq heard it"

        return R()

    monkeypatch.setattr(settings, "stt_provider", "nvidia")
    monkeypatch.setattr(settings, "nvidia_api_key", "test-nvidia-key")
    monkeypatch.setattr("navigator.voice.nvidia._urlopen", fake_urlopen)
    monkeypatch.setattr("navigator.core.groq_client.transcribe_create", fake_groq)

    from navigator.voice.stt import transcribe

    assert transcribe(b"\x00\x01" * 8, "gsk_test", language="en") == "groq heard it"
    assert groq_calls["api_key"] == "gsk_test"
    assert "falling back to Groq" in capsys.readouterr().out


def test_transcribe_nvidia_hindi_skips_http_and_uses_groq(monkeypatch):
    called = {"n": 0}

    def boom(req, timeout=0):
        called["n"] += 1
        raise AssertionError("parakeet is english-only")

    def fake_groq(api_key=None, **kwargs):
        class R:
            text = "namaste"

        return R()

    monkeypatch.setattr(settings, "stt_provider", "nvidia")
    monkeypatch.setattr(settings, "nvidia_api_key", "test-nvidia-key")
    monkeypatch.setattr("navigator.voice.nvidia._urlopen", boom)
    monkeypatch.setattr("navigator.core.groq_client.transcribe_create", fake_groq)

    from navigator.voice.stt import transcribe

    assert transcribe(b"\x00\x01" * 8, "gsk_test", language="hi") == "namaste"
    assert called["n"] == 0


def test_transcribe_default_provider_does_not_call_nvidia(monkeypatch):
    monkeypatch.setattr(settings, "stt_provider", "groq")

    def boom(req, timeout=0):
        raise AssertionError("nvidia stt should not run")

    monkeypatch.setattr("navigator.voice.nvidia._urlopen", boom)

    from navigator.voice import stt as stt_mod

    class R:
        text = "whisper"

    monkeypatch.setattr(
        "navigator.core.groq_client.transcribe_create",
        lambda api_key=None, **kwargs: R(),
    )
    assert stt_mod.transcribe(b"\x01\x00" * 4, "gsk_test") == "whisper"


def test_riva_transcript_shape(monkeypatch):
    payload = {"results": [{"alternatives": [{"transcript": "from riva"}]}]}

    def fake_urlopen(req, timeout=0):
        return _Body(json.dumps(payload).encode())

    monkeypatch.setattr(settings, "stt_provider", "nvidia")
    monkeypatch.setattr(settings, "nvidia_api_key", "test-nvidia-key")
    monkeypatch.setattr("navigator.voice.nvidia._urlopen", fake_urlopen)

    from navigator.voice.stt import transcribe

    assert transcribe(b"\x00\x01" * 8, "gsk", language="en") == "from riva"


def test_finish_nvidia_turn_unblocks_say_wait():
    bridge = _Bridge()
    agent = LiveAgent(LiveAgentConfig(api_key="x", system_instruction="s"), bridge)
    try:
        events = []
        agent.cfg.on_event = events.append
        agent._turn_done.clear()
        agent._finish_nvidia_turn("Hello there friend", 1.25)
        assert agent._turn_done.is_set()
        assert agent._turn_said == ["Hello there friend"]
        assert agent._last_turn_audio_s == 1.25
        assert bridge.ended is True
        assert [e.kind for e in events] == ["said", "turn_complete"]
    finally:
        agent._loop.close()


async def test_pump_cmd_nvidia_does_not_send_text_to_gemini(monkeypatch):
    monkeypatch.setattr(settings, "voice_provider", "nvidia")
    bridge = _Bridge()
    agent = LiveAgent(LiveAgentConfig(api_key="x", system_instruction="s"), bridge)
    sent: list[str] = []

    class Session:
        async def send_realtime_input(self, text: str) -> None:
            sent.append(text)
            agent._stop.set()

    def fake_tts(bridge_arg, text, *, language, cancel=None, on_first_audio=None):
        bridge_arg.push_outbound_pcm(b"\x01\x00" * 8, sample_rate=24000, writer="nvidia-tts")
        if on_first_audio is not None:
            on_first_audio()
        return 0.4

    monkeypatch.setattr("navigator.voice.tts.try_nvidia_tts", fake_tts)

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
    assert bridge.chunks[0][2] == "nvidia-tts"
    assert agent._turn_said[-1] == "Phrased by the brain"


async def test_pump_cmd_nvidia_error_falls_back_to_gemini(monkeypatch):
    monkeypatch.setattr(settings, "voice_provider", "nvidia")
    bridge = _Bridge()
    agent = LiveAgent(LiveAgentConfig(api_key="x", system_instruction="s", language="en"), bridge)
    sent: list[str] = []

    class Session:
        async def send_realtime_input(self, text: str) -> None:
            sent.append(text)
            agent._stop.set()

    monkeypatch.setattr("navigator.voice.tts.try_nvidia_tts", lambda *a, **k: None)

    async def _run() -> None:
        agent._cmds.put(_Cmd(kind="say", text="Say this line"))
        await asyncio.wait_for(agent._pump_cmd(Session()), timeout=2)

    try:
        await _run()
    finally:
        agent._loop.close()
    assert sent
    assert "Say this line" in sent[0]
    assert bridge.chunks == []


def test_nvidia_voice_error_carries_status():
    err = NvidiaVoiceError("HTTP 429: busy", status=429)
    assert err.status == 429
    assert "429" in str(err)
