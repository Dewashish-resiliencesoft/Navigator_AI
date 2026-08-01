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
