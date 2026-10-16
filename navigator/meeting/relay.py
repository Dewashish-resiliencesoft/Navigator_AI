"""Local HTTP pages that mirror a Playwright viewport for Attendee.

`/agent`  — blank mic page for Attendee camera tile (no 3D avatar).
`/view`   — capture page Attendee loads (video + agent audio, or JPEG fallback).
`/screen.ws` — latest CDP frame and agent PCM for the video page.
`/status` — JSON status (kept for API; /view no longer polls it).
`/frame.jpg` — latest Playwright JPEG (jpeg mode).

Default ``NAVIGATOR_SCREENSHARE_MODE=video``: ``/view`` is a ``<video>`` fed by
``canvas.captureStream`` plus an AudioContext, so Attendee's ximagesrc+alsasrc
grab shares one clock. ``jpeg`` keeps the polled ``/frame.jpg`` page.
Chromium's screencast API only emits JPEG; Playwright stays in this process.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.sync_api import Page


def fps_for_meeting_url(url: str | None) -> int:
    """Capture rate for one meeting link. The frame size stays 1920×1080.

    ``meet.google.com`` is 30fps (Meet's screen-share cap). Zoom, Teams, and
    local runs (no link) are 60fps.
    """
    raw = (url or "").strip()
    if not raw:
        return 60
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower()
    if host == "meet.google.com" or host.endswith(".meet.google.com"):
        return 30
    return 60


def _fps_override() -> int | None:
    """``NAVIGATOR_SCREENSHARE_FPS`` when set. None means use the meeting URL."""
    from navigator.core.settings import settings

    raw = getattr(settings, "screenshare_fps", None)
    if raw is None or raw == "":
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return max(1, min(60, n))


def _attendee_max_fps() -> int:
    """``NAVIGATOR_ATTENDEE_MAX_FPS``. Default 15 matches Attendee's grab."""
    from navigator.core.settings import settings

    raw = getattr(settings, "attendee_max_fps", 15)
    try:
        n = int(15 if raw is None else raw)
    except (TypeError, ValueError):
        n = 15
    return max(1, min(60, n or 15))


def resolve_capture_fps(meeting_url: str | None) -> int:
    """Frames this session encodes. Passed a URL; nothing is process-global.

    ``min(platform fps, NAVIGATOR_SCREENSHARE_FPS if set, NAVIGATOR_ATTENDEE_MAX_FPS)``.
    Platform is 30 on Google Meet and 60 on Zoom, Teams, and local. The
    Attendee cap defaults to 15 because the webpage streamer hardcodes
    ``framerate=15/1`` — faster encodes are dropped. Raise
    ``NAVIGATOR_ATTENDEE_MAX_FPS`` to 30 or 60 once that Attendee-repo patch
    is deployed.

    60fps through the public tunnel floods Cloudflare and starves Gemini
    Live voice. 15fps leaves CPU and bandwidth for clear audio.
    """
    parts = [fps_for_meeting_url(meeting_url), _attendee_max_fps()]
    override = _fps_override()
    if override is not None:
        parts.append(override)
    return max(1, min(parts))


def relay_fps_cap(meeting_url: str | None = None) -> int:
    """Capture rate for one meeting link. ``None`` is a local (no-link) session."""
    return resolve_capture_fps(meeting_url)


def view_frame_ms(target_fps: int | None = None, meeting_url: str | None = None) -> int:
    """Poll interval for /view → frame.jpg (ms), for this meeting link only.

    The ceiling is :func:`resolve_capture_fps`, not a fixed 8–24fps band.
    ``target_fps`` still asks for a slower poll under that ceiling.
    """
    cap = resolve_capture_fps(meeting_url)
    if target_fps is None:
        target_fps = cap
    fps = max(1, min(cap, int(target_fps)))
    return max(1, int(round(1000.0 / fps)))


def screencast_every_nth(target_fps: int | None = None, meeting_url: str | None = None) -> int:
    """Drop CDP frames when the capture rate is below the compositor's ~60fps.

    Video mode follows :func:`resolve_capture_fps` for ``meeting_url``.
    Floor, not round, so a rate is never subsampled below what was asked.
    An explicit ``target_fps`` is clamped to that same per-link ceiling.
    """
    cap = resolve_capture_fps(meeting_url)
    if target_fps is None:
        from navigator.core.settings import settings
        from navigator.meeting.screen_page import video_mode

        if video_mode():
            return max(1, int(60 / cap))

        forced = int(getattr(settings, "screencast_every_nth", 0) or 0)
        if forced > 0:
            return forced

        target_fps = cap
    fps = max(1, min(cap, int(target_fps)))
    return max(1, int(round(60.0 / fps)))


def _mjpeg_on() -> bool:
    from navigator.core.settings import settings

    return bool(getattr(settings, "screencast_mjpeg", True))


def screencast_max_size() -> tuple[int, int]:
    """CDP frame size. Same viewport the agent and the capture page use."""
    from navigator.automation.browser.viewport import viewport_px

    return viewport_px()


#: Defaults used by tests / callers that import constants (resolved at import
#: from settings — prefer view_frame_ms() / screencast_every_nth() at runtime).
VIEW_FRAME_MS = view_frame_ms()
SCREENCAST_EVERY_NTH_FRAME = screencast_every_nth()


_AGENT_HTML = """<!doctype html>
<html><head><meta charset=utf-8><title>Navigator</title>
<style>html,body{margin:0;width:__W__px;height:__H__px;background:#0b1220;overflow:hidden}</style>
</head><body>
<script>navigator.mediaDevices.getUserMedia({audio:true}).catch(()=>{});</script>
</body></html>
"""


@dataclass
class _ScreenClient:
    audio: deque = field(default_factory=lambda: deque(maxlen=80))
    clear: bool = False


@dataclass
class RelayHandle:
    host: str
    port: int
    _httpd: ThreadingHTTPServer
    _thread: threading.Thread
    _frame: bytes = b""
    _lock: threading.Lock = field(default_factory=threading.Lock)
    frame_hits: int = 0
    view_hits: int = 0
    screencast: bool = False
    status_mode: str = "demo"
    status_label: str = "Demo"
    avatar_state: str = "idle"
    last_frame_at: float = 0.0
    #: ``time.monotonic_ns()`` when ``_frame`` was captured. Same clock as audio.
    frame_monotonic_ns: int = 0
    _seq: int = 0
    _cond: threading.Condition = field(default_factory=threading.Condition)
    _closed: bool = False
    stream_clients: int = 0
    frames_streamed: int = 0
    screen_clients: int = 0
    _screen_clients: list = field(default_factory=list)
    _screen_lock: threading.Lock = field(default_factory=threading.Lock)
    _wake: int = 0
    #: This demo's link. Fps is derived from it so two relays cannot share a rate.
    meeting_url: str = ""

    def capture_fps(self) -> int:
        return resolve_capture_fps(self.meeting_url)

    def set_frame(self, data: bytes) -> None:
        """Replace the latest frame and wake MJPEG streams (no queue)."""
        from navigator.meeting.media_clock import now_ns

        stamp = now_ns()
        with self._lock:
            self._frame = data
            self.frame_monotonic_ns = stamp
            self.last_frame_at = time.time()
        with self._cond:
            self._seq += 1
            self._wake += 1
            self._cond.notify_all()

    def register_screen_client(self) -> _ScreenClient:
        client = _ScreenClient()
        with self._screen_lock:
            self._screen_clients.append(client)
            self.screen_clients = len(self._screen_clients)
        return client

    def unregister_screen_client(self, client: _ScreenClient) -> None:
        with self._screen_lock:
            self._screen_clients = [c for c in self._screen_clients if c is not client]
            self.screen_clients = len(self._screen_clients)

    def push_pcm(self, pcm: bytes, sample_rate: int) -> None:
        """Queue agent PCM for every capture-page websocket (not the bot mic)."""
        if not pcm or sample_rate <= 0:
            return
        if len(pcm) % 2:
            pcm = pcm[:-1]
        if not pcm:
            return
        msg = b"A" + int(sample_rate).to_bytes(4, "little") + pcm
        with self._screen_lock:
            for client in self._screen_clients:
                client.audio.append(msg)
        with self._cond:
            self._wake += 1
            self._cond.notify_all()

    def clear_pcm(self) -> None:
        """Barge-in: drop queued page audio and tell the page to reset its clock."""
        with self._screen_lock:
            for client in self._screen_clients:
                client.audio.clear()
                client.clear = True
        with self._cond:
            self._wake += 1
            self._cond.notify_all()

    def wait_screen(self, timeout: float) -> None:
        with self._cond:
            self._cond.wait(timeout=timeout)

    def wait_screen_since(self, seen_wake: int, timeout: float) -> None:
        """Sleep only when nothing new arrived after ``seen_wake``.

        The check and the wait share ``_cond``, so a frame that lands during
        send cannot be missed and turn into a multi-frame stall.
        """
        with self._cond:
            if self._closed or self._wake != seen_wake:
                return
            self._cond.wait(timeout=timeout)

    def drain_screen_client(
        self, client: _ScreenClient
    ) -> tuple[bytes, int, list[bytes], bool]:
        with self._lock:
            frame, seq = self._frame, self._seq
        with self._screen_lock:
            audio = list(client.audio)
            client.audio.clear()
            clear = client.clear
            client.clear = False
        return frame, seq, audio, clear

    def note_frame_sent(self) -> None:
        self.frames_streamed += 1

    @property
    def view_url(self) -> str:
        return f"http://{self.host}:{self.port}/view"

    @property
    def agent_url(self) -> str:
        return f"http://{self.host}:{self.port}/agent"

    def set_status(self, mode: str, label: str | None = None) -> None:
        mode = (mode or "demo").strip().lower() or "demo"
        self.status_mode = mode
        self.status_label = label or mode.replace("_", " ").title()

    def set_avatar_state(self, state: str) -> None:
        self.avatar_state = (state or "idle").strip().lower() or "idle"

    def stop(self) -> None:
        self._closed = True
        with self._cond:
            self._cond.notify_all()
        self._httpd.shutdown()
        self._thread.join(timeout=5)


def start_relay(
    host: str = "127.0.0.1", port: int = 0, meeting_url: str | None = None
) -> RelayHandle:
    """Start the HTTP server. Push frames from the Playwright thread via push_frame.

    ``meeting_url`` is stored on the handle. Omit it to snapshot
    ``settings.meeting_url`` once — later demos do not inherit this relay's link.
    """
    if meeting_url is None:
        from navigator.core.settings import settings

        meeting_url = getattr(settings, "meeting_url", "") or ""
    meeting_url = str(meeting_url or "").strip()
    holder: dict[str, RelayHandle] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object, **_kwargs: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            handle = holder["h"]
            path = self.path.split("?", 1)[0]
            if path == "/screen.ws":
                key = (self.headers.get("Sec-WebSocket-Key") or "").strip()
                if (self.headers.get("Upgrade") or "").lower() != "websocket" or not key:
                    self.send_response(400)
                    self.end_headers()
                    return
                from navigator.meeting.screen_page import serve_screen_socket, ws_accept

                self.send_response(101, "Switching Protocols")
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", ws_accept(key))
                self.end_headers()
                self.close_connection = True
                try:
                    self.wfile.flush()
                except OSError:
                    return
                serve_screen_socket(self.connection, handle)
                return
            if path.startswith("/agent"):
                from navigator.automation.browser.viewport import viewport_px

                w, h = viewport_px()
                body = (
                    _AGENT_HTML.replace("__W__", str(w)).replace("__H__", str(h)).encode("utf-8")
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/view"):
                handle.view_hits += 1
                from navigator.meeting.screen_page import view_page_html

                body = view_page_html(meeting_url=handle.meeting_url).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/status"):
                payload = json.dumps(
                    {
                        "mode": handle.status_mode,
                        "label": handle.status_label,
                        "frame_monotonic_ns": int(handle.frame_monotonic_ns or 0),
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if path.startswith("/stream"):
                handle.stream_clients += 1
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=navframe")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                seen = -1
                try:
                    while not handle._closed:
                        with handle._cond:
                            # Latest frame only: wait for a newer seq, skip stale ones.
                            if handle._seq == seen:
                                handle._cond.wait(timeout=2.0)
                            data, seq = handle._frame, handle._seq
                        if not data or seq == seen:
                            continue
                        seen = seq
                        stamp = str(int(handle.frame_monotonic_ns or 0)).encode()
                        self.wfile.write(
                            b"--navframe\r\nContent-Type: image/jpeg\r\n"
                            b"X-Navigator-Monotonic-Ns: " + stamp + b"\r\nContent-Length: "
                            + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n"
                        )
                        self.wfile.flush()
                        handle.frames_streamed += 1
                        handle.frame_hits += 1  # MJPEG counts as a pull too
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    handle.stream_clients -= 1
                return
            if path.startswith("/frame"):
                handle.frame_hits += 1
                with handle._lock:
                    data = handle._frame
                    stamp = int(handle.frame_monotonic_ns or 0)
                if not data:
                    self.send_response(204)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Navigator-Monotonic-Ns", str(stamp))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            self.send_response(404)
            self.end_headers()

    httpd = ThreadingHTTPServer((host, port), Handler)
    real_port = int(httpd.server_address[1])
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    )
    handle = RelayHandle(
        host=host, port=real_port, _httpd=httpd, _thread=thread, meeting_url=meeting_url
    )
    holder["h"] = handle
    thread.start()
    global LAST_HANDLE
    LAST_HANDLE = handle
    return handle


LAST_HANDLE: "RelayHandle | None" = None


def offer_voice_to_capture_page(pcm: bytes, sample_rate: int) -> bool:
    """Play agent PCM inside ``/view`` when Attendee is capturing that page.

    True means the page accepted the chunk. The caller must not also send it
    on the Attendee bot-output websocket, or the meeting hears two clocks.
    """
    from navigator.meeting.screen_page import video_mode

    if not video_mode():
        return False
    h = LAST_HANDLE
    if h is None or h._closed or h.screen_clients <= 0:
        return False
    h.push_pcm(pcm, sample_rate)
    if not getattr(offer_voice_to_capture_page, "_logged", False):
        offer_voice_to_capture_page._logged = True  # type: ignore[attr-defined]
        print("[live] voice playing on capture page (one clock with video)", flush=True)
    return True


def wait_frame_sent(timeout_s: float = 0.7) -> bool:
    """Block until the viewer has been sent a frame newer than now.

    Narration waits on this so words never run ahead of the screen."""
    h = LAST_HANDLE
    if h is None or h._closed or (h.stream_clients <= 0 and h.screen_clients <= 0):
        return False
    start = h.frames_streamed
    end = time.time() + timeout_s
    while time.time() < end:
        if h.frames_streamed > start:
            return True
        time.sleep(0.02)
    return False


def start_screencast(handle: RelayHandle, page: Page):
    """Stream repaints into ``handle`` via CDP instead of polling screenshots.

    A `page.screenshot()` costs ~30ms of the Playwright thread — the same thread
    that has to keep 24kHz PCM flowing — so the old per-hop push capped motion at
    ~12fps and overran its own timing by ~1.5x. Chromium pushes screencast frames
    on repaint; we subsample via everyNthFrame. Video mode follows
    NAVIGATOR_SCREENSHARE_FPS (no 8–24fps cap). JPEG mode follows
    NAVIGATOR_TARGET_FPS.

    Returns the CDP session, or None if screencast is unavailable (the caller
    then keeps using push_frame).
    """
    from navigator.core.settings import settings

    quality = max(1, min(100, int(settings.screenshot_quality or 70)))
    nth = screencast_every_nth(meeting_url=handle.meeting_url)
    max_w, max_h = screencast_max_size()
    try:
        cdp = page.context.new_cdp_session(page)
    except Exception as exc:  # noqa: BLE001
        print(f"[live] screencast unavailable, using screenshots: {exc}", flush=True)
        return None

    def _on_frame(event: dict) -> None:
        data = event.get("data")
        if data:
            handle.set_frame(base64.b64decode(data))
        # Chromium stops sending until the frame is acked.
        try:
            cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})
        except Exception:  # noqa: BLE001
            pass

    try:
        cdp.on("Page.screencastFrame", _on_frame)
        cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": quality,
                "maxWidth": max_w,
                "maxHeight": max_h,
                "everyNthFrame": nth,
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[live] startScreencast failed, using screenshots: {exc}", flush=True)
        return None
    from navigator.meeting.screen_page import video_mode

    cadence = (
        f"capture_fps={handle.capture_fps()}"
        if video_mode()
        else f"view_ms={view_frame_ms(meeting_url=handle.meeting_url)}"
    )
    print(
        f"[live] screencast=on quality={quality} everyNth={nth} "
        f"size={max_w}x{max_h} {cadence}",
        flush=True,
    )
    handle.screencast = True
    return cdp


def stop_screencast(cdp) -> None:
    if cdp is None:
        return
    try:
        cdp.send("Page.stopScreencast")
    except Exception:  # noqa: BLE001
        pass


def push_frame(handle: RelayHandle, page: Page) -> None:
    """Screenshot on the Playwright thread only (sync API is not thread-safe)."""
    from navigator.core.settings import settings

    # Screencast already encoded the repaint; the frame just has to be collected.
    # Playwright's sync client only dispatches CDP events while it is inside a
    # call, so yield to it briefly (~2ms) instead of taking a ~30ms screenshot.
    if handle.screencast:
        try:
            page.wait_for_timeout(1)
        except Exception:  # noqa: BLE001
            pass
        return
    quality = max(50, min(100, int(settings.screenshot_quality or 95)))
    from navigator.automation.browser.viewport import viewport_px

    w, h = viewport_px()
    try:
        data = page.screenshot(
            type="jpeg",
            quality=quality,
            clip={"x": 0, "y": 0, "width": w, "height": h},
        )
    except Exception:
        data = page.screenshot(type="jpeg", quality=quality)
    handle.set_frame(data)
