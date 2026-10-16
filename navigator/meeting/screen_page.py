"""Page Attendee loads so screenshare video and agent voice share one capture.

Attendee's webpage streamer opens the screenshare URL in Chrome and grabs
that display with one GStreamer pipeline: ``ximagesrc`` plus ``alsasrc``,
one clock, both tracks on one WebRTC offer. The window size is
``WEBPAGE_STREAMER_VIDEO_FRAME_SIZE`` (ops set ``1920x1080``). Meet then
caps the share at 1080p30.

The streamer's pipeline still hardcodes ``framerate=15/1``. Raising that
grab to 30fps is a patch in the Attendee repo, not in Navigator.

Playwright stays in this process. CDP frames are pushed on ``/screen.ws``
and painted into a ``<video>`` via ``canvas.captureStream`` at the rate for
that session's meeting link: 30fps on ``meet.google.com``, 60fps on Zoom,
Teams, and local, then clamped to
``min(platform, NAVIGATOR_SCREENSHARE_FPS, NAVIGATOR_ATTENDEE_MAX_FPS)``.
The Attendee ceiling defaults to 15 so we do not encode frames the
hardcoded grab drops. 60fps through the public tunnel floods Cloudflare
and starves Gemini Live voice; raise ``NAVIGATOR_ATTENDEE_MAX_FPS`` to 30
or 60 after the Attendee-repo framerate patch is deployed. There is no
fixed 8–24fps poll cap. The viewport stays 1920×1080.
Agent PCM is played by an AudioContext in the same document, so the grab
hears the voice. ``NAVIGATOR_SCREENSHARE_MODE=jpeg`` restores the polled
``/frame.jpg`` page and leaves voice on the Attendee bot-output websocket.

``/screen.ws`` binary messages, either layout (merge order with the shared
clock does not matter):

- Timestamped audio: ``A`` | uint32le sample_rate | uint64le monotonic_ns | PCM
- Timestamped video: ``F`` | uint64le monotonic_ns | JPEG
- Legacy audio: ``A`` | uint32le sample_rate | PCM
- Legacy video: ``F`` | JPEG, or a raw JPEG with no tag
- Clear: ``C``

Both audio forms start with ``A`` and a uint32le rate, so the tag byte alone
cannot split them. A JPEG SOI (``FF D8``) at offset 9 marks a timestamped
frame; SOI at offset 1, or no SOI, is legacy. Audio queued before the first
frame follows that frame's layout. Stamps schedule PCM against the frame
that is on screen.
"""

from __future__ import annotations

import base64
import hashlib
import select
import socket
from typing import Any

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_JPEG_VIEW = """<!doctype html>
<html><head><meta charset=utf-8><title>Navigator screen</title>
<style>
html,body{margin:0;background:#000;width:__W__px;height:__H__px;overflow:hidden}
img{width:__W__px;height:__H__px;object-fit:fill;display:block;image-rendering:auto}
</style></head>
<body>
<img id=f alt=frame width=__W__ height=__H__>
<script>
/* Interval poll with in-flight skip: never backlog stale JPEGs when the
   Cloudflare hop is slow (serial await stretched the old cadence). */
let inFlight = false;
async function pullFrame(){
  if (inFlight) return;
  inFlight = true;
  try {
    const r = await fetch('frame.jpg?ts='+Date.now(), {cache:'no-store'});
    if (r.ok) {
      const b = await r.blob();
      const url = URL.createObjectURL(b);
      const img = document.getElementById('f');
      const old = img.src;
      img.src = url;
      if (old && old.startsWith('blob:')) URL.revokeObjectURL(old);
    }
  } catch (e) {}
  inFlight = false;
}
let polling = null;
function startPolling(){ if (!polling) { polling = setInterval(pullFrame, __VIEW_FRAME_MS__); pullFrame(); } }
if (__MJPEG__) {
  /* MJPEG push: server sends only the newest frame when it changes. */
  const img = document.getElementById('f');
  let got = false;
  img.onload = () => { got = true; };
  img.onerror = () => startPolling();
  img.src = 'stream?ts=' + Date.now();
  setTimeout(() => { if (!got) { img.src = ''; startPolling(); } }, 4000);
} else { startPolling(); }
</script></body></html>
"""

_VIDEO_VIEW = """<!doctype html>
<html><head><meta charset=utf-8><title>Navigator screen</title>
<style>
html,body{margin:0;background:#000;width:__W__px;height:__H__px;overflow:hidden}
video,canvas{position:absolute;left:0;top:0;width:__W__px;height:__H__px;background:#000}
canvas{opacity:0;pointer-events:none}
</style></head>
<body>
<canvas id=c width=__W__ height=__H__></canvas>
<video id=v autoplay playsinline muted></video>
<script>
const W=__W__, H=__H__, FPS=__FPS__;
const canvas = document.getElementById('c');
const video = document.getElementById('v');
const ctx = canvas.getContext('2d');
ctx.fillStyle = '#000';
ctx.fillRect(0, 0, W, H);
const stream = canvas.captureStream(FPS);
video.srcObject = stream;
video.play().catch(() => {});
const audioCtx = new AudioContext();
let nextTime = 0;
let lastBitmap = null;
let decoding = false;
let pending = null;
function paint(bmp){ ctx.drawImage(bmp, 0, 0, W, H); }
function loop(){ if (lastBitmap) paint(lastBitmap); requestAnimationFrame(loop); }
loop();
let layout = '';
let queued = [];
let clockNs = 0;
let clockAt = 0;
let haveClock = false;
function u64le(view, offset){
  return view.getUint32(offset, true) + view.getUint32(offset + 4, true) * 4294967296;
}
function soi(u, offset){
  return offset + 1 < u.length && u[offset] === 0xFF && u[offset + 1] === 0xD8;
}
function playPcm(rate, bytes, ns){
  if (!rate || !bytes || bytes.byteLength < 2) return;
  audioCtx.resume();
  const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength >> 1);
  const buf = audioCtx.createBuffer(1, pcm.length, rate);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  src.connect(audioCtx.destination);
  let start = Math.max(audioCtx.currentTime + 0.02, nextTime);
  if (ns !== null && haveClock) {
    const when = clockAt + (ns - clockNs) / 1e9;
    if (when >= audioCtx.currentTime - 0.05) start = Math.max(start, when);
  }
  src.start(start);
  nextTime = start + buf.duration;
}
function takeAudio(u){
  const view = new DataView(u.buffer, u.byteOffset, u.byteLength);
  const rate = u.length >= 5 ? view.getUint32(1, true) : 0;
  if (layout === 'S' && u.length >= 13) {
    const n = u.length - 13 - ((u.length - 13) % 2);
    return {rate: rate, ns: u64le(view, 5), pcm: n >= 2 ? u.subarray(13, 13 + n) : null};
  }
  const n = u.length - 5 - ((u.length - 5) % 2);
  return {rate: rate, ns: null, pcm: n >= 2 ? u.subarray(5, 5 + n) : null};
}
function takeVideo(u){
  if (u.length >= 2 && u[0] === 0xFF && u[1] === 0xD8) return {ns: null, jpeg: u};
  if (u[0] !== 70) return null;
  if (u.length >= 11 && soi(u, 9)) {
    const view = new DataView(u.buffer, u.byteOffset, u.byteLength);
    return {ns: u64le(view, 1), jpeg: u.subarray(9)};
  }
  return {ns: null, jpeg: u.subarray(1)};
}
function showVideo(parsed){
  if (!parsed) return;
  if (!layout) layout = parsed.ns === null ? 'L' : 'S';
  if (parsed.ns !== null) {
    clockNs = parsed.ns;
    clockAt = audioCtx.currentTime;
    haveClock = true;
  }
  const blob = new Blob([parsed.jpeg], {type: 'image/jpeg'});
  if (decoding) pending = blob;
  else decode(blob);
  const waiting = queued;
  queued = [];
  for (let i = 0; i < waiting.length; i++) onPayload(waiting[i]);
}
function onPayload(u){
  if (!u.length) return;
  const kind = u[0];
  if (kind === 67) { nextTime = 0; queued = []; return; }
  if (kind === 70 || (kind === 0xFF && u.length > 1 && u[1] === 0xD8)) {
    showVideo(takeVideo(u));
    return;
  }
  if (kind === 65) {
    if (!layout) {
      queued.push(u.slice());
      if (queued.length > 32) {
        layout = 'L';
        const waiting = queued;
        queued = [];
        for (let i = 0; i < waiting.length; i++) onPayload(waiting[i]);
      }
      return;
    }
    const a = takeAudio(u);
    if (a.pcm) playPcm(a.rate, a.pcm, a.ns);
  }
}
async function decode(blob){
  decoding = true;
  try {
    const bmp = await createImageBitmap(blob);
    if (lastBitmap && lastBitmap.close) lastBitmap.close();
    lastBitmap = bmp;
    paint(bmp);
  } catch (e) {}
  decoding = false;
  if (pending) { const b = pending; pending = null; decode(b); }
}
const wsProto = location.protocol === 'https:' ? 'wss://' : 'ws://';
const ws = new WebSocket(wsProto + location.host + '/screen.ws');
ws.binaryType = 'arraybuffer';
ws.onmessage = (ev) => { onPayload(new Uint8Array(ev.data)); };
</script></body></html>
"""


# Widths from the shared-clock spec (PR #35 media_clock.py).
_U32 = 4
_U64 = 8
AUDIO_TAG = b"A"
FRAME_TAG = b"F"
CLEAR_TAG = b"C"
STAMPED_AUDIO_HEADER = 1 + _U32 + _U64  # 13: tag, uint32le rate, uint64le ns
LEGACY_AUDIO_HEADER = 1 + _U32  # 5: tag, uint32le rate
STAMPED_FRAME_HEADER = 1 + _U64  # 9: tag, uint64le ns
_JPEG_SOI = b"\xff\xd8"
_AUDIO_QUEUE_CAP = 32
SYNC_LATE_S = 0.05


class ScreenMessage:
    """One decoded ``/screen.ws`` payload."""

    __slots__ = ("kind", "sample_rate", "monotonic_ns", "body")

    def __init__(
        self,
        kind: str,
        *,
        sample_rate: int | None = None,
        monotonic_ns: int | None = None,
        body: bytes = b"",
    ) -> None:
        self.kind = kind
        self.sample_rate = sample_rate
        self.monotonic_ns = monotonic_ns
        self.body = body


def presentation_delay_s(media_ns: int, anchor_ns: int) -> float:
    """Seconds after the anchor frame when a stamped chunk should start.

    Negative means the chunk is already late relative to the frame on screen.
    The page plays those immediately instead of scheduling them in the past.
    """
    return (int(media_ns) - int(anchor_ns)) / 1_000_000_000


def _even_pcm(payload: bytes, offset: int) -> bytes:
    body = payload[offset:]
    if len(body) % 2:
        body = body[:-1]
    return body


def _soi_at(payload: bytes, offset: int) -> bool:
    return offset >= 0 and payload[offset : offset + 2] == _JPEG_SOI


def parse_screen_payload(payload: bytes, *, stamped: bool | None = None) -> ScreenMessage:
    """Decode one capture-page message.

    ``stamped=True`` forces the timestamped audio header. ``stamped=False``
    or ``None`` reads legacy audio (``A`` + uint32le rate + PCM). Video does
    not use the flag: a JPEG SOI at offset 9 is timestamped, anything else
    after ``F`` is legacy. A raw SOI with no tag is a legacy frame. ``C``
    clears the play clock.
    """
    if not payload:
        return ScreenMessage("ignore")
    tag = payload[:1]
    if tag == CLEAR_TAG:
        return ScreenMessage("clear")
    if tag == FRAME_TAG or _soi_at(payload, 0):
        return _parse_video(payload)
    if tag == AUDIO_TAG:
        return _parse_audio(payload, stamped=bool(stamped))
    return ScreenMessage("ignore")


def _parse_video(payload: bytes) -> ScreenMessage:
    if _soi_at(payload, 0):
        return ScreenMessage("video", body=payload)
    if len(payload) >= STAMPED_FRAME_HEADER + 2 and _soi_at(payload, STAMPED_FRAME_HEADER):
        stamp = int.from_bytes(payload[1:STAMPED_FRAME_HEADER], "little")
        return ScreenMessage("video", monotonic_ns=stamp, body=payload[STAMPED_FRAME_HEADER:])
    return ScreenMessage("video", body=payload[1:])


def _parse_audio(payload: bytes, *, stamped: bool) -> ScreenMessage:
    if len(payload) < LEGACY_AUDIO_HEADER:
        return ScreenMessage("audio", sample_rate=0, body=b"")
    rate = int.from_bytes(payload[1:LEGACY_AUDIO_HEADER], "little")
    if stamped and len(payload) >= STAMPED_AUDIO_HEADER:
        stamp = int.from_bytes(payload[LEGACY_AUDIO_HEADER:STAMPED_AUDIO_HEADER], "little")
        return ScreenMessage(
            "audio",
            sample_rate=rate,
            monotonic_ns=stamp,
            body=_even_pcm(payload, STAMPED_AUDIO_HEADER),
        )
    return ScreenMessage(
        "audio",
        sample_rate=rate,
        monotonic_ns=None,
        body=_even_pcm(payload, LEGACY_AUDIO_HEADER),
    )


class ScreenClockReader:
    """Stream reader. The first video frame picks legacy or timestamped audio.

    Audio that arrives before that frame is held (up to 32 messages). A
    leading ``A`` or ``F`` is tagged; any other byte is legacy only when it
    is a raw JPEG.
    """

    def __init__(self) -> None:
        self.stamped: bool | None = None
        self.clock_ns: int | None = None
        self._pending: list[bytes] = []

    def feed(self, payload: bytes) -> list[ScreenMessage]:
        if not payload:
            return []
        tag = payload[:1]
        if tag == CLEAR_TAG:
            self._pending.clear()
            return [ScreenMessage("clear")]
        if tag == FRAME_TAG or _soi_at(payload, 0):
            video = _parse_video(payload)
            if self.stamped is None:
                self.stamped = video.monotonic_ns is not None
            if video.monotonic_ns is not None:
                self.clock_ns = video.monotonic_ns
            pending, self._pending = self._pending, []
            audio = [
                _parse_audio(item, stamped=bool(self.stamped)) for item in pending
            ]
            return [video, *audio]
        if tag == AUDIO_TAG:
            if self.stamped is None:
                self._pending.append(payload)
                if len(self._pending) > _AUDIO_QUEUE_CAP:
                    self.stamped = False
                    pending, self._pending = self._pending, []
                    return [_parse_audio(item, stamped=False) for item in pending]
                return []
            return [_parse_audio(payload, stamped=self.stamped)]
        return []


def video_mode() -> bool:
    from navigator.core.settings import settings

    mode = str(getattr(settings, "screenshare_mode", "video") or "video").strip().lower()
    return mode != "jpeg"


def delivered_capture_fps(meeting_url: str | None = None) -> int:
    """Paint rate for one session's capture page. See ``resolve_capture_fps``."""
    from navigator.meeting.relay import resolve_capture_fps

    return resolve_capture_fps(meeting_url)


def capture_pump_ms(meeting_url: str | None = None) -> int:
    """How often a Playwright caller must wake so CDP is not held below this session.

    Half a frame, at least 1ms. JPEG mode keeps a 20ms pump.
    """
    if not video_mode():
        return 20
    return max(1, int(1000 / delivered_capture_fps(meeting_url) / 2))


def view_page_html(
    *,
    jpeg: bool | None = None,
    frame_ms: int | None = None,
    mjpeg: bool | None = None,
    meeting_url: str | None = None,
) -> str:
    """HTML for ``/view``. Video mode is the default; jpeg keeps the poll path."""
    from navigator.automation.browser.viewport import viewport_px

    w, h = viewport_px()
    use_jpeg = (not video_mode()) if jpeg is None else jpeg
    if use_jpeg:
        if frame_ms is None or mjpeg is None:
            from navigator.meeting.relay import _mjpeg_on, view_frame_ms

            if frame_ms is None:
                frame_ms = view_frame_ms(meeting_url=meeting_url)
            if mjpeg is None:
                mjpeg = _mjpeg_on()
        return (
            _JPEG_VIEW.replace("__W__", str(w))
            .replace("__H__", str(h))
            .replace("__VIEW_FRAME_MS__", str(frame_ms))
            .replace("__MJPEG__", "true" if mjpeg else "false")
        )
    return (
        _VIDEO_VIEW.replace("__W__", str(w))
        .replace("__H__", str(h))
        .replace("__FPS__", str(delivered_capture_fps(meeting_url)))
    )


def ws_accept(key: str) -> str:
    digest = hashlib.sha1((key.strip() + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_server_frame(opcode: int, payload: bytes) -> bytes:
    n = len(payload)
    head = bytearray((0x80 | (opcode & 0x0F),))
    if n < 126:
        head.append(n)
    elif n < 65536:
        head.append(126)
        head.extend(n.to_bytes(2, "big"))
    else:
        head.append(127)
        head.extend(n.to_bytes(8, "big"))
    head.extend(payload)
    return bytes(head)


def pop_client_frame(buf: bytearray) -> tuple[int, bytes] | None:
    """Pop one client frame. Client frames are masked. None if incomplete."""
    if len(buf) < 2:
        return None
    opcode = buf[0] & 0x0F
    masked = buf[1] & 0x80
    ln = buf[1] & 0x7F
    i = 2
    if ln == 126:
        if len(buf) < 4:
            return None
        ln = int.from_bytes(buf[2:4], "big")
        i = 4
    elif ln == 127:
        if len(buf) < 10:
            return None
        ln = int.from_bytes(buf[2:10], "big")
        i = 10
    if ln > 1_000_000:
        raise ValueError("websocket frame too large")
    mask_len = 4 if masked else 0
    if len(buf) < i + mask_len + ln:
        return None
    mask = bytes(buf[i : i + mask_len]) if masked else b""
    i += mask_len
    data = bytearray(buf[i : i + ln])
    del buf[: i + ln]
    if mask:
        for j in range(len(data)):
            data[j] ^= mask[j % 4]
    return opcode, bytes(data)


def _drain_client_socket(sock: socket.socket, incoming: bytearray) -> bool:
    """Read waiting client frames without blocking. False means stop."""
    while True:
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return True
        try:
            chunk = sock.recv(4096)
        except OSError:
            return False
        if not chunk:
            return False
        incoming.extend(chunk)
        try:
            while True:
                item = pop_client_frame(incoming)
                if item is None:
                    break
                opcode, payload = item
                if opcode == 8:
                    return False
                if opcode == 9:
                    sock.sendall(encode_server_frame(10, payload))
        except ValueError:
            return False


def serve_screen_socket(sock: socket.socket, handle: Any) -> None:
    """Push the latest JPEG and agent PCM until the socket closes.

    Latest video frame only. Audio is a short queue so a slow client drops
    old speech instead of stalling the screencast thread.

    The loop does not sleep between frames. A blocking recv here used to
    hold the next JPEG for 200ms (~5fps) no matter what
    ``NAVIGATOR_SCREENSHARE_FPS`` said.
    """
    client = handle.register_screen_client()
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    sock.settimeout(None)
    seen = -1
    incoming = bytearray()
    try:
        while not handle._closed:
            with handle._cond:
                wake = handle._wake
            frame, seq, audio, clear = handle.drain_screen_client(client)
            try:
                if clear:
                    sock.sendall(encode_server_frame(2, b"C"))
                for msg in audio:
                    sock.sendall(encode_server_frame(2, msg))
                if frame and seq != seen:
                    sock.sendall(encode_server_frame(2, b"F" + frame))
                    seen = seq
                    handle.note_frame_sent()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            if not _drain_client_socket(sock, incoming):
                return
            # Idle only. A newer frame bumps ``_wake`` under the same lock
            # and returns immediately, so 60fps is not quantized to this wait.
            handle.wait_screen_since(wake, 0.02)
    finally:
        handle.unregister_screen_client(client)
