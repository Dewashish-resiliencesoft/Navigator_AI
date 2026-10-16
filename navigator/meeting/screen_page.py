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

``/screen.ws`` switches on the first byte only. There is no sniffing and no
hold for a later frame:

- ``A`` legacy audio: uint32le sample_rate | int16le PCM (PCM at offset 5)
- ``F`` legacy video: JPEG (at offset 1)
- ``a`` timestamped audio: uint32le sample_rate | uint64le monotonic_ns | PCM
  (PCM at offset 13)
- ``f`` timestamped video: uint64le monotonic_ns | JPEG (JPEG at offset 9)
- ``C`` barge-in: drop queued PCM, stop scheduled sources, reset the play clock
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
const sources = [];
let clockNs = 0;
let clockAt = 0;
let haveClock = false;
function u64le(view, offset){
  return view.getUint32(offset, true) + view.getUint32(offset + 4, true) * 4294967296;
}
function evenPcm(u, offset){
  let n = u.length - offset;
  if (n < 2) return null;
  n -= n % 2;
  return u.subarray(offset, offset + n);
}
function bargeIn(){
  nextTime = 0;
  clockNs = 0;
  clockAt = 0;
  haveClock = false;
  while (sources.length) {
    const src = sources.pop();
    try { src.stop(0); } catch (e) {}
  }
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
  src.onended = () => {
    const i = sources.indexOf(src);
    if (i >= 0) sources.splice(i, 1);
  };
  sources.push(src);
  let start = Math.max(audioCtx.currentTime + 0.02, nextTime);
  if (ns !== null && haveClock) {
    const when = clockAt + (ns - clockNs) / 1e9;
    if (when >= audioCtx.currentTime - 0.05) start = Math.max(start, when);
  }
  src.start(start);
  nextTime = start + buf.duration;
}
function showJpeg(jpeg, ns){
  if (ns !== null) {
    clockNs = ns;
    clockAt = audioCtx.currentTime;
    haveClock = true;
  }
  if (!jpeg || jpeg.length < 2) return;
  const blob = new Blob([jpeg], {type: 'image/jpeg'});
  if (decoding) pending = blob;
  else decode(blob);
}
function onPayload(u){
  if (!u.length) return;
  const kind = u[0];
  const view = new DataView(u.buffer, u.byteOffset, u.byteLength);
  if (kind === 67) { bargeIn(); return; }
  if (kind === 65) {
    const pcm = evenPcm(u, 5);
    if (pcm) playPcm(view.getUint32(1, true), pcm, null);
    return;
  }
  if (kind === 97) {
    const pcm = evenPcm(u, 13);
    if (pcm) playPcm(view.getUint32(1, true), pcm, u64le(view, 5));
    return;
  }
  if (kind === 70) { showJpeg(u.subarray(1), null); return; }
  if (kind === 102) {
    const ns = u.length >= 9 ? u64le(view, 1) : 0;
    showJpeg(u.subarray(9), ns);
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


# First-byte tags. Case is the layout. Nothing else is inspected.
_U32 = 4
_U64 = 8
LEGACY_AUDIO_TAG = b"A"
STAMPED_AUDIO_TAG = b"a"
LEGACY_FRAME_TAG = b"F"
STAMPED_FRAME_TAG = b"f"
CLEAR_TAG = b"C"
LEGACY_AUDIO_HEADER = 1 + _U32  # 5: tag, uint32le rate; PCM follows
STAMPED_AUDIO_HEADER = 1 + _U32 + _U64  # 13: tag, rate, uint64le ns; PCM follows
STAMPED_FRAME_HEADER = 1 + _U64  # 9: tag, uint64le ns; JPEG follows
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


def parse_screen_payload(payload: bytes) -> ScreenMessage:
    """Decode one capture-page message from its first byte.

    ``A`` / ``F`` are legacy. ``a`` / ``f`` carry ``uint64le`` monotonic_ns.
    ``C`` is barge-in. Any other leading byte is ignored.
    """
    if not payload:
        return ScreenMessage("ignore")
    tag = payload[:1]
    if tag == CLEAR_TAG:
        return ScreenMessage("clear")
    if tag == LEGACY_AUDIO_TAG:
        return _parse_audio(payload, stamped=False)
    if tag == STAMPED_AUDIO_TAG:
        return _parse_audio(payload, stamped=True)
    if tag == LEGACY_FRAME_TAG:
        return ScreenMessage("video", body=payload[1:])
    if tag == STAMPED_FRAME_TAG:
        if len(payload) < STAMPED_FRAME_HEADER:
            return ScreenMessage("video", monotonic_ns=0, body=b"")
        stamp = int.from_bytes(payload[1:STAMPED_FRAME_HEADER], "little")
        return ScreenMessage(
            "video", monotonic_ns=stamp, body=payload[STAMPED_FRAME_HEADER:]
        )
    return ScreenMessage("ignore")


def _parse_audio(payload: bytes, *, stamped: bool) -> ScreenMessage:
    if len(payload) < LEGACY_AUDIO_HEADER:
        return ScreenMessage("audio", sample_rate=0, body=b"")
    rate = int.from_bytes(payload[1:LEGACY_AUDIO_HEADER], "little")
    if stamped:
        if len(payload) < STAMPED_AUDIO_HEADER:
            return ScreenMessage("audio", sample_rate=rate, monotonic_ns=0, body=b"")
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
        body=_even_pcm(payload, LEGACY_AUDIO_HEADER),
    )


class ScreenClockReader:
    """Page play state. Each message is applied immediately from its tag.

    ``C`` drops PCM waiting to play and resets the play offset and the
    frame anchor, which is what the capture page does to its
    ``AudioBufferSourceNode`` list.
    """

    def __init__(self) -> None:
        self.scheduled: list[ScreenMessage] = []
        self.play_offset_s = 0.0
        self.clock_ns: int | None = None

    def feed(self, payload: bytes) -> ScreenMessage:
        msg = parse_screen_payload(payload)
        if msg.kind == "clear":
            self.scheduled.clear()
            self.play_offset_s = 0.0
            self.clock_ns = None
            return msg
        if msg.kind == "video" and msg.monotonic_ns is not None:
            self.clock_ns = msg.monotonic_ns
        elif msg.kind == "audio" and msg.body and msg.sample_rate:
            self.scheduled.append(msg)
            self.play_offset_s += len(msg.body) / (2.0 * msg.sample_rate)
        return msg


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
