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
import os
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
    """Return (pcm, out_rate).

    NOT used on the send path any more: Attendee's RealtimeAudioOutputManager
    returns a ``(chunk, rate)`` tuple when the incoming rate already equals its
    output rate (48 kHz on Zoom web / Meet), and ``send_raw_audio`` then dies
    with ``TypeError: a bytes-like object is required, not 'tuple'`` on every
    chunk — the bot is silent. Sending Gemini's native 24 kHz lets Attendee do
    an integer 2x upsample that returns bytes.
    """
    if not pcm or src_rate == dst_rate:
        return pcm, src_rate
    return upsample_pcm16_mono(pcm, src_rate, dst_rate), dst_rate


_PATCH_OK: bool | None = None


def _attendee_48k_ok() -> bool:
    """True when Attendee's same-rate tuple bug is patched (see attendee_ws_patch)."""
    global _PATCH_OK
    if _PATCH_OK is None:
        try:
            from pathlib import Path

            from navigator.meeting.attendee_stack import _compose_dir
            from navigator.meeting.attendee_ws_patch import _UPS_MARKER

            f = Path(_compose_dir()) / "bots" / "bot_controller" / "realtime_audio_output_manager.py"
            _PATCH_OK = f.is_file() and _UPS_MARKER in f.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            _PATCH_OK = False
        print(f"[voice-out] attendee 48k passthrough={'on' if _PATCH_OK else 'off (24k native)'}", flush=True)
    return _PATCH_OK


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Band-limited-ish resample (linear interp, vectorised). No sample doubling."""
    import numpy as np

    if not pcm or src_rate == dst_rate:
        return pcm
    x = np.frombuffer(pcm[: len(pcm) - (len(pcm) % 2)], dtype=np.int16).astype(np.float32)
    if x.size == 0:
        return b""
    n_out = int(round(x.size * dst_rate / src_rate))
    t = np.arange(n_out, dtype=np.float64) * (src_rate / dst_rate)
    y = np.interp(t, np.arange(x.size), x)
    return np.clip(y, -32768, 32767).astype(np.int16).tobytes()


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
        self.pcm_in_bytes = 0
        #: True once Attendee sends per-participant audio (excludes our voice).
        self.echo_free = False
        self.bytes_sent = 0
        self.send_errors = 0
        self._last_out_log = 0.0

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
        self.pcm_in_bytes += len(pcm or b"")
        self._tap_pcm(pcm or b"", sample_rate)
        self._outbound.put((pcm, sample_rate))

    def mark_line_end(self) -> None:
        """Model finished a line. The silence before the next line's first chunk
        is a pause between utterances (incl. LLM/TTS latency), not an underrun.
        Before this, ~1-1.9s gaps at every line start were counted as underruns."""
        self._tap_play_end = 0.0

    def _tap_pcm(self, pcm: bytes, rate: int) -> None:
        """Underrun detector + optional WAV tap (NAVIGATOR_VOICE_WAV=/tmp/x.wav)."""
        now = time.monotonic()
        play_end = getattr(self, "_tap_play_end", 0.0)
        if not play_end:
            self.line_starts = getattr(self, "line_starts", 0) + 1
        elif 0.0 < now - play_end < 2.0:
            # Gemini chunk arrived after the previous audio already ran out.
            self.underruns = getattr(self, "underruns", 0) + 1
            print(f"[voice-out] underrun gap_ms={(now - play_end) * 1000:.0f} n={self.underruns}", flush=True)
        self._tap_play_end = max(now, play_end) + len(pcm) / (2.0 * rate)
        path = os.environ.get("NAVIGATOR_VOICE_WAV")
        if not path:
            return
        import wave

        w = getattr(self, "_tap_wav", None)
        if w is None:
            w = wave.open(path, "wb")
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            self._tap_wav = w
        w.writeframes(pcm)

    def _log_out(self, rate: int) -> None:
        now = time.monotonic()
        if now - self._last_out_log < 3.0:
            return
        self._last_out_log = now
        print(
            f"[voice-out] gemini pcm bytes={self.pcm_in_bytes} | sent to attendee "
            f"chunks={self.chunks_sent} bytes={self.bytes_sent} sample_rate={rate} "
            f"audio_s={self.audio_s_sent:.1f} errors={self.send_errors}",
            flush=True,
        )

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
        except Exception as exc:  # noqa: BLE001
            self.send_errors = getattr(self, "send_errors", 0) + 1
            if self.send_errors <= 3 or self.send_errors % 50 == 0:
                print(f"[voice-out] ws send error #{self.send_errors}: {exc}", flush=True)
            return False

    def _send_pcm(self, pcm: bytes, rate: int) -> None:
        if not pcm:
            return
        duration_s = _pcm_seconds(pcm, rate)
        # 48 kHz with a proper interpolating resample when Attendee is patched
        # (it then passes bytes through untouched); else native rate and let
        # Attendee duplicate samples (crashes avoided, but slightly harsher).
        if _attendee_48k_ok() and rate != _ATTENDEE_PLAYBACK_RATE:
            out, out_rate = resample_pcm16(pcm, rate, _ATTENDEE_PLAYBACK_RATE), _ATTENDEE_PLAYBACK_RATE
        else:
            out, out_rate = pcm, rate
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
                self.bytes_sent += len(out)
                self.audio_s_sent += duration_s
                self._log_out(out_rate)
                if self.chunks_sent == 1:
                    print(
                        "[voice-out] zoom bot mic state=unmuting — Attendee clicks "
                        "'unmute my microphone' on first bot_output (it never did "
                        "before: send_raw_audio crashed). If the tile stays muted the "
                        "host has muted it / disallowed self-unmute: ask the host to "
                        "unmute Navigator or enable 'Allow participants to unmute themselves'.",
                        flush=True,
                    )
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        self.send_errors += 1
        print(
            "[voice-out] ws send error: dropped chunk, Attendee WS not connected",
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
            pcm_in = base64.b64decode(chunk_b64)
            if msg.get("trigger") == "realtime_audio.per_participant":
                self.echo_free = True
            self.inbound.put(pcm_in)
            self.chunks_received += 1
            self.pcm_recv_bytes = getattr(self, "pcm_recv_bytes", 0) + len(pcm_in)
            now = time.monotonic()
            if now - getattr(self, "_last_in_log", 0.0) >= 5.0:
                self._last_in_log = now
                print(
                    f"[voice-in] pcm bytes={self.pcm_recv_bytes} chunks={self.chunks_received} "
                    f"trigger={msg.get('trigger')!r} rate={data.get('sample_rate')}",
                    flush=True,
                )
            if self.chunks_received in (1, 50, 200):
                print(
                    f"[audio] pcm chunks received={self.chunks_received} "
                    f"trigger={msg.get('trigger')!r}",
                    flush=True,
                )
        except Exception:  # noqa: BLE001
            return
