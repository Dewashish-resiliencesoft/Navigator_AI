"""Audio chunks and video frames share one monotonic clock."""

from __future__ import annotations

import threading
import time

from navigator.core.settings import settings
import pytest

from navigator.meeting.media_clock import (
    pack_audio,
    pack_clear,
    pack_frame,
    unpack_audio,
    unpack_frame,
)
from navigator.meeting.relay import start_relay
from navigator.voice.live_agent import LiveAgent, LiveAgentConfig, _Cmd
from navigator.voice.stt import meeting_stt_mode


def test_audio_and_frame_payloads_share_an_increasing_stamp():
    audio_ns = time.monotonic_ns()
    audio = pack_audio(b"\x01\x00\x02\x00", 24000, audio_ns)
    frame = pack_frame(b"\xff\xd8\xff", audio_ns + 1)
    rate, got_audio_ns, pcm = unpack_audio(audio)
    got_frame_ns, jpeg = unpack_frame(frame)
    assert rate == 24000
    assert pcm == b"\x01\x00\x02\x00"
    assert jpeg == b"\xff\xd8\xff"
    assert got_frame_ns >= got_audio_ns
    assert audio[:1] == b"a" and frame[:1] == b"f"
    assert audio[1:13] == (24000).to_bytes(4, "little") + audio_ns.to_bytes(8, "little")
    assert frame[1:9] == (audio_ns + 1).to_bytes(8, "little")


def test_legacy_tags_stay_untimestamped():
    """``A`` and ``F`` have no stamp. Those bytes stay in the payload."""
    pcm = (99).to_bytes(8, "little") + b"\x01\x00"
    legacy_audio = b"A" + (16000).to_bytes(4, "little") + pcm
    rate, stamp, body = unpack_audio(legacy_audio)
    assert rate == 16000
    assert stamp == 0
    assert body == pcm

    jpeg = b"\x00" * 8 + b"\xff\xd8\xff"
    legacy_frame = b"F" + jpeg
    frame_ns, got = unpack_frame(legacy_frame)
    assert frame_ns == 0
    assert got == jpeg


def test_clear_is_its_own_tag():
    assert pack_clear() == b"C"
    with pytest.raises(ValueError):
        unpack_audio(b"C")
    with pytest.raises(ValueError):
        unpack_frame(b"C")


def test_bot_output_json_includes_monotonic_ns():
    from navigator.meeting.audio_bridge import AudioBridge

    sent: dict = {}
    bridge = AudioBridge.__new__(AudioBridge)
    bridge._ws = object()
    bridge._send_lock = threading.Lock()
    bridge._stop = threading.Event()
    bridge.send_errors = 0
    bridge.chunks_sent = 0
    bridge.bytes_sent = 0
    bridge.audio_s_sent = 0.0
    bridge._last_out_log = time.monotonic()
    bridge._send_json = lambda payload: sent.update(payload) or True
    bridge._log_out = lambda _rate: None
    bridge._send_pcm(b"\x01\x00" * 20, 24000)
    data = sent["data"]
    assert data["monotonic_ns"] > 0
    assert data["sample_rate"] > 0
    assert data["chunk"]
    assert bridge.last_audio_monotonic_ns == data["monotonic_ns"]


def test_frame_stamp_is_on_the_relay():
    relay = start_relay()
    try:
        before = time.monotonic_ns()
        relay.set_frame(b"\xff\xd8jpeg")
        assert relay.frame_monotonic_ns >= before
        packed = pack_frame(b"\xff\xd8jpeg", relay.frame_monotonic_ns)
        stamp, jpeg = unpack_frame(packed)
        assert stamp == relay.frame_monotonic_ns
        assert jpeg.startswith(b"\xff\xd8")
    finally:
        relay.stop()


def test_barge_in_flushes_playback_and_drops_the_next_clause():
    bridge_calls = {"flush": 0}

    class Bridge:
        def flush_bot_output(self) -> None:
            bridge_calls["flush"] += 1

    agent = LiveAgent(LiveAgentConfig(api_key="t", system_instruction="t"), Bridge())
    try:
        agent._cmds.put(_Cmd(kind="say", text="next clause"))
        agent._cmds.put(_Cmd(kind="context", text="keep this"))
        agent.speaking = True
        agent.playback_phase = "playing"
        now = time.monotonic()
        agent._audio_rx_at = now
        agent._vad_feed(5000, now)
        agent._vad_feed(5000, now + 0.2)
        assert agent.interrupted is True
        assert bridge_calls["flush"] == 1
        left = []
        while True:
            try:
                left.append(agent._cmds.get_nowait().kind)
            except Exception:
                break
        assert left == ["context"]
    finally:
        agent._loop.close()


def test_meeting_stt_mode_is_partial_unless_nvidia(monkeypatch):
    monkeypatch.setattr(settings, "stt_provider", "groq")
    assert meeting_stt_mode() == "partial"
    assert settings.live_stt_min_silence_ms == 400
    monkeypatch.setattr(settings, "stt_provider", "nvidia")
    assert meeting_stt_mode() == "batch"
