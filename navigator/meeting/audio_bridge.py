"""Local websocket hub: Attendee pushes Meet PCM here; we can push bot output.

ponytail: one threaded sync websockets server. Ceiling: single connection handler
loop. Upgrade: per-bot hubs.

Outbound path (glitch fixes):
1. Coalesce ~100ms of PCM — Gemini Live emits tiny chunks; shipping each through
   Cloudflare live-ws causes Zoom/Meet underruns.
2. Upsample 24 kHz → 48 kHz — Zoom web AudioContext is 48 kHz; mismatched
   AudioBuffers click when the browser resamples many tiny buffers.
"""

from __future__ import annotations

import array
import base64
import json
import threading
import time
from collections.abc import Iterator
from queue import Empty, Queue
from typing import Any

#: Coalesce bot PCM before Attendee (~100ms at source rate).
_OUTBOUND_COALESCE_S = 0.10
#: Zoom web virtual mic AudioContext is 48 kHz.
_ATTENDEE_PLAYBACK_RATE = 48_000


def _pcm_seconds(pcm: bytes, sample_rate: int) -> float:
    """Playback duration of a 16-bit mono PCM chunk."""
    return len(pcm) / float(max(1, sample_rate) * 2)


def _coalesce_bytes(sample_rate: int, seconds: float = _OUTBOUND_COALESCE_S) -> int:
    return max(640, int(sample_rate * 2 * seconds))


def upsample_pcm16_mono(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear upsample int16 mono. Exact 2× (24→48k) uses sample doubling."""
    if not pcm or src_rate <= 0 or src_rate == dst_rate:
        return pcm
    if len(pcm) % 2:
        pcm = pcm[:-1]
    if not pcm:
        return pcm
    src = array.array("h")
    src.frombytes(pcm)
    if dst_rate == src_rate * 2:
        # Exact 2× — zero-order hold is enough and click-free for speech.
        out = array.array("h", [0]) * (len(src) * 2)
        j = 0
        for s in src:
            out[j] = s
            out[j + 1] = s
            j += 2
        return out.tobytes()
    # Generic linear (rare path).
    n_out = max(1, int(round(len(src) * dst_rate / src_rate)))
    out = array.array("h", [0]) * n_out
    last = len(src) - 1
    for i in range(n_out):
        pos = i * (len(src) - 1) / max(1, n_out - 1)
        i0 = int(pos)
        i1 = min(i0 + 1, last)
        frac = pos - i0
        out[i] = int(src[i0] * (1 - frac) + src[i1] * frac)
    return out.tobytes()


def upsample_for_attendee(
    pcm: bytes,
    src_rate: int,
    *,
    dst_rate: int = _ATTENDEE_PLAYBACK_RATE,
) -> tuple[bytes, int]:
    """Return (pcm, out_rate)."""
    if not pcm or src_rate == dst_rate:
        return pcm, src_rate
    return upsample_pcm16_mono(pcm, src_rate, dst_rate), dst_rate


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
        #: Bumped on flush_bot_output so the sender drops its coalesce buffer.
        self._out_epoch = 0
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
        self._out_epoch += 1
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

    def _send_pcm(self, pcm: bytes, rate: int) -> None:
        if not pcm:
            return
        duration_s = _pcm_seconds(pcm, rate)
        out, out_rate = upsample_for_attendee(pcm, rate)
        payload = {
            "trigger": "realtime_audio.bot_output",
            "data": {
                "chunk": base64.b64encode(out).decode(),
                "sample_rate": out_rate,
            },
        }
        # Handshake can finish a tick before the handler assigns self._ws.
        deadline = time.monotonic() + 45.0
        while not self._stop.is_set():
            if self._send_json(payload):
                self.chunks_sent += 1
                self.audio_s_sent += duration_s
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        print(
            "[audio] dropped outbound chunk: Attendee WS not connected",
            flush=True,
        )

    def _wait_ws(self) -> None:
        deadline = time.monotonic() + 45.0
        while self._ws is None and not self._stop.is_set():
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)

    def _run_sender(self) -> None:
        """Push bot audio coalesced (~100ms) + upsampled as soon as queued."""
        buf = bytearray()
        buf_rate = 0
        epoch = self._out_epoch
        while not self._stop.is_set():
            if self._out_epoch != epoch:
                buf.clear()
                buf_rate = 0
                epoch = self._out_epoch
            try:
                pcm, rate = self._outbound.get(timeout=0.03)
            except Empty:
                if buf and buf_rate and self._out_epoch == epoch:
                    self._wait_ws()
                    if self._out_epoch == epoch:
                        self._send_pcm(bytes(buf), buf_rate)
                    buf.clear()
                    buf_rate = 0
                continue
            if self._out_epoch != epoch:
                buf.clear()
                buf_rate = 0
                epoch = self._out_epoch
                continue
            self._wait_ws()
            if buf_rate and rate != buf_rate:
                self._send_pcm(bytes(buf), buf_rate)
                buf.clear()
            buf_rate = rate
            buf.extend(pcm)
            need = _coalesce_bytes(rate)
            while len(buf) >= need and self._out_epoch == epoch:
                piece = bytes(buf[:need])
                del buf[:need]
                self._send_pcm(piece, rate)
            if not buf:
                buf_rate = 0
            if self._out_epoch != epoch:
                buf.clear()
                buf_rate = 0
                epoch = self._out_epoch

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
