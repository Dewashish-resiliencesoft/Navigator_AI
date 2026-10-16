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
function playPcm(rate, bytes){
  if (!rate || bytes.byteLength < 2) return;
  audioCtx.resume();
  const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength >> 1);
  const buf = audioCtx.createBuffer(1, pcm.length, rate);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  src.connect(audioCtx.destination);
  const start = Math.max(audioCtx.currentTime + 0.02, nextTime);
  src.start(start);
  nextTime = start + buf.duration;
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
ws.onmessage = (ev) => {
  const u = new Uint8Array(ev.data);
  const kind = u[0];
  if (kind === 67) { nextTime = 0; return; }
  if (kind === 65) {
    const view = new DataView(u.buffer, u.byteOffset, u.byteLength);
    playPcm(view.getUint32(1, true), u.subarray(5));
    return;
  }
  if (kind === 70) {
    const blob = new Blob([u.subarray(1)], {type: 'image/jpeg'});
    if (decoding) pending = blob;
    else decode(blob);
  }
};
</script></body></html>
"""


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
