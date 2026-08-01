"""Local websocket hub: Attendee pushes Meet PCM here; we can push bot output.

ponytail: one threaded sync websockets server. Ceiling: single connection handler
loop. Upgrade: per-bot hubs.
"""

from __future__ import annotations

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
        self._server: Any = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.clients_connected = 0
        self.chunks_received = 0

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
        server = self._server
        if server is not None:
            try:
                server.shutdown()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._server = None

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
        except ImportError as exc:
            print(f"[audio] websockets missing: {exc}", flush=True)
            self._ready.set()
            return

        def handler(ws: Any) -> None:
            self.clients_connected += 1
            print(
                f"[audio] Attendee websocket connected (clients={self.clients_connected})",
                flush=True,
            )
            try:
                for raw in ws:
                    if self._stop.is_set():
                        break
                    self._handle_message(raw)
                    # Flush any pending bot_output (rarely used; speak uses HTTP).
                    while True:
                        try:
                            pcm, rate = self._outbound.get_nowait()
                        except Empty:
                            break
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
                            return
            except Exception:  # noqa: BLE001
                return

        try:
            with sync_serve(handler, self.host, self.port) as server:
                self._server = server
                self.port = int(server.socket.getsockname()[1])
                self._ready.set()
                # Critical: without serve_forever(), TCP listens but never accepts.
                server.serve_forever()
        except Exception as exc:  # noqa: BLE001
            print(f"[audio] bridge failed: {exc}", flush=True)
            self._ready.set()

    def _handle_message(self, raw: Any) -> None:
        if isinstance(raw, bytes):
            self.inbound.put(raw)
            self.chunks_received += 1
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
            self.chunks_received += 1
            if self.chunks_received in (1, 50, 200):
                print(
                    f"[audio] pcm chunks received={self.chunks_received} "
                    f"trigger={msg.get('trigger')!r}",
                    flush=True,
                )
        except Exception:  # noqa: BLE001
            return
