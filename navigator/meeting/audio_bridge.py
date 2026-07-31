"""Local websocket hub: Attendee pushes Meet PCM here; we can push bot output.

ponytail: one threaded asyncio loop. Ceiling: single connection. Upgrade: per-bot hubs.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from collections.abc import Iterator
from queue import Empty, Queue
from typing import Any


class AudioBridge:
    """Receives Attendee realtime_audio chunks into ``inbound`` queue."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.inbound: Queue[bytes] = Queue()
        self._outbound: Queue[tuple[bytes, int]] = Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()

    @property
    def local_url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    def start(self) -> AudioBridge:
        self._thread = threading.Thread(
            target=self._run, name="audio-bridge", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError("AudioBridge failed to start")
        return self

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    def push_outbound_pcm(self, pcm: bytes, *, sample_rate: int = 16000) -> None:
        self._outbound.put((pcm, sample_rate))

    def frames(self, *, timeout_s: float | None = None) -> Iterator[bytes]:
        while not self._stop.is_set():
            try:
                yield self.inbound.get(timeout=0.5 if timeout_s is None else timeout_s)
            except Empty:
                if timeout_s is not None:
                    return

    def _run(self) -> None:
        try:
            from websockets.sync.server import serve as sync_serve
        except ImportError:
            try:
                self._run_async()
                return
            except Exception as exc:  # noqa: BLE001
                print(f"[audio] bridge start failed: {exc}", flush=True)
                self._ready.set()
                return

        # Sync server is simpler from a worker thread.
        def handler(ws: Any) -> None:
            def reader() -> None:
                try:
                    for raw in ws:
                        self._handle_message(raw)
                except Exception:  # noqa: BLE001
                    return

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            while not self._stop.is_set():
                try:
                    pcm, rate = self._outbound.get(timeout=0.2)
                except Empty:
                    continue
                try:
                    ws.send(
                        json.dumps(
                            {
                                "trigger": "realtime_audio.bot_output",
                                "data": {
                                    "chunk": base64.b64encode(pcm).decode(),
                                    "sample_rate": rate,
                                },
                            }
                        )
                    )
                except Exception:  # noqa: BLE001
                    break
            t.join(timeout=1)

        with sync_serve(handler, self.host, self.port) as server:
            self.port = int(server.socket.getsockname()[1])
            self._ready.set()
            while not self._stop.is_set():
                self._stop.wait(0.2)

    def _run_async(self) -> None:
        from websockets.asyncio.server import serve

        async def handler(ws: Any) -> None:
            async def consume() -> None:
                async for raw in ws:
                    self._handle_message(raw)

            async def produce() -> None:
                while not self._stop.is_set():
                    try:
                        pcm, rate = await asyncio.to_thread(
                            self._outbound.get, True, 0.2
                        )
                    except Empty:
                        continue
                    await ws.send(
                        json.dumps(
                            {
                                "trigger": "realtime_audio.bot_output",
                                "data": {
                                    "chunk": base64.b64encode(pcm).decode(),
                                    "sample_rate": rate,
                                },
                            }
                        )
                    )

            await asyncio.gather(consume(), produce())

        async def main() -> None:
            async with serve(handler, self.host, self.port) as server:
                socks = server.sockets or []
                if socks:
                    self.port = int(socks[0].getsockname()[1])
                self._ready.set()
                while not self._stop.is_set():
                    await asyncio.sleep(0.2)

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(main())
        finally:
            self._loop.close()
            self._loop = None

    def _handle_message(self, raw: Any) -> None:
        if isinstance(raw, bytes):
            self.inbound.put(raw)
            return
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        data = msg.get("data") or {}
        chunk_b64 = data.get("chunk")
        if not chunk_b64:
            return
        try:
            self.inbound.put(base64.b64decode(chunk_b64))
        except Exception:  # noqa: BLE001
            return
