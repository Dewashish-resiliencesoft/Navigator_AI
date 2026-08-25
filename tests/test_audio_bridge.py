"""AudioBridge must accept WebSocket connections (serve_forever)."""

from __future__ import annotations

import asyncio
import base64
import json

from navigator.meeting.audio_bridge import AudioBridge


def test_audio_bridge_accepts_and_queues_pcm():
    bridge = AudioBridge().start()
    try:

        async def client() -> None:
            import websockets

            async with websockets.connect(
                f"ws://127.0.0.1:{bridge.port}", open_timeout=5
            ) as ws:
                chunk = base64.b64encode(b"\x00\x01" * 50).decode()
                await ws.send(
                    json.dumps(
                        {
                            "trigger": "realtime_audio.mixed",
                            "data": {"chunk": chunk, "sample_rate": 16000},
                        }
                    )
                )
                await asyncio.sleep(0.2)

        asyncio.run(client())
        assert bridge.clients_connected >= 1
        assert bridge.chunks_received >= 1
        assert bridge.inbound.get(timeout=1)[:2] == b"\x00\x01"
    finally:
        bridge.stop()


def test_outbound_pcm_sends_without_any_inbound_traffic():
    """Bot audio must go out on its own, not only when the meeting sends us something.

    The old handler flushed _outbound inside the inbound read loop, so a silent
    meeting meant a silent bot.
    """
    bridge = AudioBridge().start()
    got: list[dict] = []
    try:

        async def client() -> None:
            import websockets

            async with websockets.connect(
                f"ws://127.0.0.1:{bridge.port}", open_timeout=5
            ) as ws:
                # Deliberately send nothing. Only listen.
                bridge.push_outbound_pcm(b"\x02\x03" * 40, sample_rate=24000)
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                got.append(json.loads(raw))

        asyncio.run(client())
        assert got, "no bot_output frame received"
        assert got[0]["trigger"] == "realtime_audio.bot_output"
        assert got[0]["data"]["sample_rate"] == 24000
        assert base64.b64decode(got[0]["data"]["chunk"])[:2] == b"\x02\x03"
    finally:
        bridge.stop()


def test_flush_bot_output_drops_queue_and_signals_attendee():
    bridge = AudioBridge().start()
    got: list[dict] = []
    try:

        async def client() -> None:
            import websockets

            async with websockets.connect(
                f"ws://127.0.0.1:{bridge.port}", open_timeout=5
            ) as ws:
                await asyncio.sleep(0.1)
                bridge.flush_bot_output()
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                got.append(json.loads(raw))

        asyncio.run(client())
        assert got[0]["trigger"] == "realtime_audio.bot_output_clear"
    finally:
        bridge.stop()


def test_clear_outbound_drops_pending_chunks():
    bridge = AudioBridge()  # not started - nothing drains the queue
    bridge.push_outbound_pcm(b"\x00" * 10)
    bridge.push_outbound_pcm(b"\x00" * 10)
    assert bridge.clear_outbound() == 2
    assert bridge.clear_outbound() == 0


def test_audio_s_sent_counts_playback_seconds():
    """Callers time actions against this, so it must be real playback seconds."""
    bridge = AudioBridge().start()
    try:

        async def client() -> None:
            import websockets

            async with websockets.connect(
                f"ws://127.0.0.1:{bridge.port}", open_timeout=5
            ) as ws:
                # 24000 samples of 16-bit mono at 24 kHz = exactly 1.0s.
                bridge.push_outbound_pcm(b"\x00\x01" * 24000, sample_rate=24000)
                await asyncio.wait_for(ws.recv(), timeout=5)

        asyncio.run(client())
        assert bridge.audio_s_sent == 1.0
    finally:
        bridge.stop()


def test_dropped_audio_is_never_counted():
    """Flushed chunks are never heard, so waiting them out would stall the demo."""
    bridge = AudioBridge()  # not started - nothing drains the queue
    bridge.push_outbound_pcm(b"\x00\x01" * 24000, sample_rate=24000)
    bridge.clear_outbound()
    assert bridge.audio_s_sent == 0.0


def test_outbound_holds_until_attendee_ws_connects():
    """Zoom ZAK join often connects tens of seconds after Gemini starts speaking."""
    bridge = AudioBridge().start()
    got: list[dict] = []
    try:

        async def client() -> None:
            import websockets

            await asyncio.sleep(3)
            async with websockets.connect(
                f"ws://127.0.0.1:{bridge.port}", open_timeout=5
            ) as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                got.append(json.loads(raw))

        async def run() -> None:
            await asyncio.sleep(0.2)
            bridge.push_outbound_pcm(b"\x04\x05" * 40, sample_rate=16000)
            await client()

        asyncio.run(run())
        assert got, "late Attendee WS never received queued bot audio"
        assert got[0]["trigger"] == "realtime_audio.bot_output"
        assert base64.b64decode(got[0]["data"]["chunk"])[:2] == b"\x04\x05"
    finally:
        bridge.stop()
