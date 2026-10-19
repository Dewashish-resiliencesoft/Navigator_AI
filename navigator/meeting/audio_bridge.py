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


class Upsampler2x:  # round 11: replaces per-chunk linear interp (10 Hz edge clicks, -28 dB imaging)
    """Streaming polyphase 2x upsampler (Kaiser windowed-sinc, cutoff 11 kHz @48k).
    Keeps filter history across chunks, so chunk edges are seamless."""
    TAPS = 64  # per phase
    def __init__(self):
        import numpy as np
        n = 2 * self.TAPS - 1; m = np.arange(n) - (n - 1) / 2.0
        fc = 11000 / 48000.0
        h = 2 * fc * np.sinc(2 * fc * m) * np.kaiser(n, 8.0)
        h *= 2.0 / h.sum()  # unity passband gain after zero-stuffing
        h = np.append(h, 0.0)
        self.h0, self.h1 = h[0::2][::-1].copy(), h[1::2][::-1].copy()
        self.hist = np.zeros(self.TAPS - 1, dtype=np.float64)
    def reset(self):
        self.hist[:] = 0
    def process(self, x):
        import numpy as np
        x = np.concatenate([self.hist, np.asarray(x, dtype=np.float64)])
        from numpy.lib.stride_tricks import sliding_window_view as sw
        win = sw(x, self.TAPS)
        y = np.empty(2 * win.shape[0]); y[0::2] = win @ self.h0; y[1::2] = win @ self.h1
        self.hist = x[-(self.TAPS - 1):].copy()
        return y


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


ACTIVE: "AudioBridge | None" = None  # the meeting's bridge (for "fully heard" waits)


def wait_heard(cap_s: float = 30.0, player_latency_s: float = 0.35) -> float:
    """Block until everything sent to the meeting has been played out (plus the
    Attendee player's own latency). Returns seconds waited."""
    b = ACTIVE
    t0 = time.monotonic()
    if b is None:
        return 0.0
    while time.monotonic() - t0 < cap_s:
        end = float(getattr(b, "out_play_end", 0.0) or 0.0) + player_latency_s
        q = getattr(b, "_outbound", None)
        if time.monotonic() >= end and (q is None or q.empty()):
            break
        time.sleep(0.05)
    return time.monotonic() - t0


class AudioBridge:
    """Receives Attendee realtime_audio chunks into ``inbound`` queue."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        global ACTIVE
        ACTIVE = self
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

    def push_outbound_pcm(self, pcm: bytes, *, sample_rate: int = 16000, writer: str | None = None) -> None:
        # Single-voice audit: two different producers feeding the meeting within
        # 0.5s means two voices are being interleaved into one output.
        who = writer or threading.current_thread().name
        now = time.monotonic()
        last_who, last_at = getattr(self, "_last_writer", (None, 0.0))
        if last_who is not None and who != last_who and now - last_at < 0.5:
            self.concurrent_writers = getattr(self, "concurrent_writers", 0) + 1
            print(f"[audio-arbiter] CONCURRENT WRITERS {last_who!r} and {who!r} "
                  f"(n={self.concurrent_writers})", flush=True)
        self._last_writer = (who, now)
        self._line_rx(pcm or b"", sample_rate, now)
        self.pcm_in_bytes += len(pcm or b"")
        self._tap_pcm(pcm or b"", sample_rate)
        self._outbound.put((pcm, sample_rate))

    # ---- adaptive pre-buffer -------------------------------------------
    # Gemini sometimes generates slower than realtime (long lines, on-the-fly
    # translation). Sending each chunk as it lands then starves the meeting
    # mid-sentence. Hold the start of a line until enough audio is buffered to
    # cover the expected shortfall: need = remaining_s * (1 - rate) + 0.25s.

    def expect_line(self, seconds: float) -> None:
        """Next line is expected to last about ``seconds`` (from its text)."""
        self._line_est_s = max(0.5, float(seconds or 0.0))

    def _line_rx(self, pcm: bytes, rate: int, now: float) -> None:
        if not getattr(self, "_line_open", False):
            self._line_open = True
            self._line_ended = False
            self._line_t0 = now
            self._line_rx_s = 0.0
            self._line_holding = True
            self._line_sent_any = False
        prev = getattr(self, "_line_last_rx", now)
        if self._line_rx_s > 0:  # Gemini arrival gap within the line (instrumentation)
            self._line_max_gap = max(getattr(self, "_line_max_gap", 0.0), now - prev)
        else:
            self._line_max_gap = 0.0
        self._line_rx_s += len(pcm) / (2.0 * rate) if rate else 0.0
        self._line_last_rx = now

    def _prebuffer_need(self, now: float) -> float:
        rx = getattr(self, "_line_rx_s", 0.0)
        elapsed = max(1e-3, now - getattr(self, "_line_t0", now))
        est = getattr(self, "_line_est_s", 6.0)
        extra = float(getattr(self, "jitter_extra_s", 0.0) or 0.0)
        if elapsed < 0.35:
            # Rate unknown yet: Live's first chunks are bursty; starting on 250ms
            # gave a 444ms underrun at the start of answers (13:23 run).
            return 0.45 + extra
        r = rx / elapsed  # audio seconds per wall second
        remaining = max(0.0, est - rx)
        if r >= 1.05:
            return 0.25 + extra
        return min(5.0, max(0.25, remaining * (1.0 - r) + 0.35) + extra)

    def mark_line_end(self) -> None:
        """Model finished a line. The silence before the next line's first chunk
        is a pause between utterances (incl. LLM/TTS latency), not an underrun.
        Before this, ~1-1.9s gaps at every line start were counted as underruns."""
        self._tap_play_end = 0.0
        if getattr(self, "_line_open", False) and getattr(self, "_line_rx_s", 0.0) > 0:
            wall = max(1e-3, getattr(self, "_line_last_rx", 0.0) - getattr(self, "_line_t0", 0.0))
            print(f"[gen] line audio={self._line_rx_s:.1f}s wall={wall:.1f}s rtf={self._line_rx_s / wall:.2f} "
                  f"max_gap_ms={getattr(self, '_line_max_gap', 0.0) * 1000:.0f} "
                  f"sender_stall_max_ms={getattr(self, '_sender_stall_max', 0.0) * 1000:.0f}", flush=True)
            self._sender_stall_max = 0.0
        self._line_open = False
        self._line_ended = True
        recent = time.monotonic() - getattr(self, "_last_underrun_at", -1e9) < 120.0
        if not getattr(self, "_line_underran", False):  # clean line: shrink back slowly
            # Steady floor: keep >=300ms extra for 2 min after any underrun so a
            # slow-generation stretch doesn't re-learn it line by line.
            self.jitter_extra_s = max(0.3 if recent else 0.0, float(getattr(self, "jitter_extra_s", 0.0) or 0.0) - 0.05)
        self._line_underran = False

    def _tap_pcm(self, pcm: bytes, rate: int) -> None:
        """Underrun detector + optional WAV tap (NAVIGATOR_VOICE_WAV=/tmp/x.wav)."""
        now = time.monotonic()
        play_end = getattr(self, "_tap_play_end", 0.0)
        if not play_end:
            self.line_starts = getattr(self, "line_starts", 0) + 1
        elif 0.0 < now - play_end < 2.0:
            # Gemini chunk arrived after the previous audio already ran out.
            self.arrival_gaps = getattr(self, "arrival_gaps", 0) + 1
        self._tap_play_end = max(now, play_end) + len(pcm) / (2.0 * rate)
        # Same estimate, but NOT reset at line end: when the meeting stops
        # hearing everything sent so far (Q&A answers included).
        self.out_play_end = max(now, getattr(self, "out_play_end", 0.0)) + len(pcm) / (2.0 * rate)
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
        self.out_play_end = time.monotonic()
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
            if rate * 2 == _ATTENDEE_PLAYBACK_RATE:
                import numpy as np
                up = getattr(self, "_ups", None)
                if up is None or getattr(self, "_ups_epoch", None) != self._out_epoch:
                    up = self._ups = Upsampler2x(); self._ups_epoch = self._out_epoch
                x = np.frombuffer(pcm[: len(pcm) - (len(pcm) % 2)], dtype=np.int16)
                out = np.clip(np.round(up.process(x)), -32768, 32767).astype(np.int16).tobytes()
            else:
                out = resample_pcm16(pcm, rate, _ATTENDEE_PLAYBACK_RATE)
            out_rate = _ATTENDEE_PLAYBACK_RATE
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
        """Push bot audio coalesced (~100ms), with an adaptive start-of-line hold."""
        buf = bytearray()
        buf_rate = 0
        epoch = self._out_epoch
        play_end = 0.0  # when the audio we already sent finishes playing
        while not self._stop.is_set():
            if self._out_epoch != epoch:
                buf.clear()
                buf_rate = 0
                epoch = self._out_epoch
                play_end = 0.0  # flushed audio never plays
            _t_loop = time.monotonic()
            _st = _t_loop - getattr(self, "_sender_last_loop", _t_loop)
            if _st > getattr(self, "_sender_stall_max", 0.0):
                self._sender_stall_max = _st  # >~60ms here = GIL/CPU starvation of the sender
            self._sender_last_loop = _t_loop
            try:
                pcm, rate = self._outbound.get(timeout=0.02)
                if buf_rate and rate != buf_rate:
                    self._wait_ws()
                    play_end = self._send_timed(bytes(buf), buf_rate, play_end)
                    buf.clear()
                buf_rate = rate
                buf.extend(pcm)
            except Empty:
                pass
            if not buf or not buf_rate:
                continue
            now = time.monotonic()
            buffered = len(buf) / (2.0 * buf_rate)
            # Real play-out end includes audio still held in the prebuffer: the
            # push-time estimate ignored a 4.4s hold, so the flow clicked Login
            # while 'this is the landing page' was still to be heard (17:38 run).
            self.out_play_end = max(getattr(self, "out_play_end", 0.0), max(play_end, now) + buffered)
            holding = getattr(self, "_line_holding", False)
            if (not holding and getattr(self, "_line_open", False)
                    and getattr(self, "_line_sent_any", False) and play_end - now < 0.05):
                # Starved mid-line (gap arrived after the buffer had already
                # drained past the old re-arm check): rebuild the jitter buffer
                # instead of trickling chunks out one by one (round 10).
                self._line_holding = holding = True
                self._hold_since = 0.0
            if holding:
                ended = getattr(self, "_line_ended", False) or not getattr(self, "_line_open", False)
                ahead = max(0.0, play_end - now)
                need = self._prebuffer_need(now)
                if not getattr(self, "_hold_since", 0.0):
                    self._hold_since = now
                idle_rx = now - getattr(self, "_line_last_rx", now)
                held = now - self._hold_since
                if (ended or buffered + ahead >= need or (idle_rx > 0.4 and buffered >= 0.15)
                        or idle_rx > 1.0 or held > min(6.0, need + 1.0)):
                    if need > 0.6 and buffered + ahead < need:  # started short of the target
                        print(f"[voice-out] prebuffer {buffered * 1000:.0f}ms+{ahead * 1000:.0f}ms queued "
                              f"(need {need * 1000:.0f}ms, slow generation) — starting", flush=True)
                    self._line_holding = False
                    self._hold_since = 0.0
                else:
                    continue
            need_b = _coalesce_bytes(buf_rate)
            self._wait_ws()
            if self._out_epoch != epoch:
                continue
            while len(buf) >= need_b:
                piece = bytes(buf[:need_b])
                del buf[:need_b]
                play_end = self._send_timed(piece, buf_rate, play_end)
            if buf and self._outbound.empty():
                play_end = self._send_timed(bytes(buf), buf_rate, play_end)
                buf.clear()
            if not buf:
                buf_rate = 0
                # Starving mid-line: re-arm the hold so the next burst is buffered.
                if getattr(self, "_line_open", False) and play_end - time.monotonic() < 0.08:
                    self._line_holding = True

    def _send_timed(self, pcm: bytes, rate: int, play_end: float) -> float:
        now = time.monotonic()
        mid_line = getattr(self, "_line_sent_any", False) and getattr(self, "_line_open", False)
        if play_end and now > play_end + 0.02 and mid_line:
            self.underruns = getattr(self, "underruns", 0) + 1
            self.underrun_ms = getattr(self, "underrun_ms", 0.0) + (now - play_end) * 1000
            # Adaptive jitter buffer: grows only after real mid-line underruns.
            self.jitter_extra_s = min(0.75, max(0.3, float(getattr(self, "jitter_extra_s", 0.0) or 0.0) + 0.15))
            self._last_underrun_at = now
            self._line_underran = True
            print(f"[voice-out] underrun gap_ms={(now - play_end) * 1000:.0f} n={self.underruns} "
                  f"jitter+={self.jitter_extra_s * 1000:.0f}ms", flush=True)
        self._line_sent_any = True
        self._send_pcm(pcm, rate)
        end = max(now, play_end) + len(pcm) / (2.0 * rate)
        self.out_play_end = max(getattr(self, "out_play_end", 0.0), end)
        return end

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
            old = self._ws
            if old is not None and old is not ws:
                self.concurrent_writers = getattr(self, "concurrent_writers", 0) + 1
                print("[audio-arbiter] second Attendee audio client connected — closing the old one "
                      f"(n={self.concurrent_writers})", flush=True)
                try:
                    old.close()
                except Exception:  # noqa: BLE001
                    pass
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
