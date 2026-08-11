"""Local websocket hub: Attendee pushes Meet PCM here; we can push bot output.

ponytail: one threaded sync websockets server. Ceiling: single connection handler
loop. Upgrade: per-bot hubs.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Iterator
from queue import Empty, Queue
from typing import Any


def _pcm_seconds(pcm: bytes, sample_rate: int) -> float:
    """Playback duration of a 16-bit mono PCM chunk."""
    return len(pcm) / float(max(1, sample_rate) * 2)


class AudioBridge:
    """Receives Attendee realtime_audio chunks into ``inbound`` queue."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.inbound: Queue[bytes] = Queue()
        self._outbound: Queue[tuple[bytes, int]] = Queue()
        self._thread: threading.Thread | None = None
        self._sender: threading.Thread | None = None
        self._server: Any = None
        self._ws: Any = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.clients_connected = 0
        self.chunks_received = 0
        self.chunks_sent = 0
        #: Seconds of bot audio actually handed to Attendee. Callers compare
        #: this against wall-clock to tell whether a line has finished playing —
        #: Gemini's turn_complete only says generation stopped, and the meeting
        #: is still several buffers behind at that point.
        self.audio_s_sent = 0.0

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
        self._sender = threading.Thread(
            target=self._run_sender, name="audio-bridge-out", daemon=True
        )
        self._sender.start()
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
        if self._sender is not None:
            self._sender.join(timeout=5)
        self._thread = None
        self._sender = None
        self._server = None
        self._ws = None

    def push_outbound_pcm(self, pcm: bytes, *, sample_rate: int = 16000) -> None:
        self._outbound.put((pcm, sample_rate))

    def clear_outbound(self) -> int:
        """Drop queued bot audio we have not sent yet. Returns how many chunks."""
        dropped = 0
        while True:
            try:
                self._outbound.get_nowait()
            except Empty:
                return dropped
            dropped += 1

    def flush_bot_output(self) -> None:
        """Barge-in: drop our queue, then tell Attendee to drop its own.

        Ours alone is not enough — Attendee has already buffered chunks and the
        browser has scheduled them into the future.
        """
        dropped = self.clear_outbound()
        self._send_json({"trigger": "realtime_audio.bot_output_clear", "data": {}})
        print(f"[audio] bot output flushed (dropped {dropped} chunk(s))", flush=True)

    def _send_json(self, payload: dict) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            with self._send_lock:
                ws.send(json.dumps(payload))
            return True
        except Exception:  # noqa: BLE001
            return False

    def _run_sender(self) -> None:
        """Push bot audio as soon as it is queued.

        Previously outbound was only flushed inside the inbound read loop, so
        bot audio could not go out unless the meeting happened to be sending us
        something — silence in, silence out.
        """
        while not self._stop.is_set():
            try:
                pcm, rate = self._outbound.get(timeout=0.2)
            except Empty:
                continue
            # Audio can be queued a beat before Attendee's socket registers.
            # Hold it briefly rather than dropping the start of an utterance.
            deadline = time.monotonic() + 2.0
            while self._ws is None and not self._stop.is_set():
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
            if self._send_json(
                {
                    "trigger": "realtime_audio.bot_output",
                    "data": {
                        "chunk": base64.b64encode(pcm).decode(),
                        "sample_rate": rate,
                    },
                }
            ):
                self.chunks_sent += 1
                # Counted on the send, not on the queue put: chunks a barge-in
                # drops or a dead socket refuses are never heard, so callers
                # must not wait them out.
                self.audio_s_sent += _pcm_seconds(pcm, rate)

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
            self._ws = ws
            print(
                f"[audio] Attendee websocket connected (clients={self.clients_connected})",
                flush=True,
            )
            try:
                for raw in ws:
                    if self._stop.is_set():
                        break
                    self._handle_message(raw)
            except Exception:  # noqa: BLE001
                return
            finally:
                if self._ws is ws:
                    self._ws = None

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
