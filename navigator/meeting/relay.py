"""Local HTTP pages that mirror a Playwright viewport for Attendee.

`/agent`  — blank mic page for Attendee camera tile (no 3D avatar).
`/view`   — demo frames only (no status pill).
`/status` — JSON status (kept for API; /view no longer polls it).
`/frame.jpg` — latest Playwright JPEG.

ponytail: frames come from CDP Page.screencast (see start_screencast) — Chromium
pushes a JPEG on repaint. /view polls frame.jpg at NAVIGATOR_TARGET_FPS (default
15): 60fps through the Cloudflare live-http tunnel saturates bandwidth and
starves Gemini Live audio. push_frame seeds first paint / non-screencast demos.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import Page


def view_frame_ms(target_fps: int | None = None) -> int:
    """Poll interval for /view → frame.jpg (ms)."""
    if target_fps is None:
        from navigator.core.settings import settings

        target_fps = int(settings.target_fps or 15)
    fps = max(5, min(30, int(target_fps)))
    return max(33, int(round(1000.0 / fps)))


def screencast_every_nth(target_fps: int | None = None) -> int:
    """Drop CDP frames when target fps is low (less JPEG encode CPU)."""
    if target_fps is None:
        from navigator.core.settings import settings

        target_fps = int(settings.target_fps or 15)
    fps = max(5, min(30, int(target_fps)))
    # Chromium ~60fps source; everyNth≈60/fps.
    return max(1, int(round(60.0 / fps)))


#: Defaults used by tests / callers that import constants (resolved at import
#: from settings — prefer view_frame_ms() / screencast_every_nth() at runtime).
VIEW_FRAME_MS = view_frame_ms()
SCREENCAST_EVERY_NTH_FRAME = screencast_every_nth()


_AGENT_HTML = """<!doctype html>
<html><head><meta charset=utf-8><title>Navigator</title>
<style>html,body{margin:0;width:1280px;height:720px;background:#0b1220;overflow:hidden}</style>
</head><body>
<script>navigator.mediaDevices.getUserMedia({audio:true}).catch(()=>{});</script>
</body></html>
"""


_VIEW_HTML = """<!doctype html>
<html><head><meta charset=utf-8><title>Navigator screen</title>
<style>
html,body{margin:0;background:#000;width:1280px;height:720px;overflow:hidden}
img{width:1280px;height:720px;object-fit:fill;display:block;image-rendering:auto}
</style></head>
<body>
<img id=f alt=frame width=1280 height=720>
<script>
async function tickFrame(){
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
  setTimeout(tickFrame, __VIEW_FRAME_MS__);
}
tickFrame();
</script></body></html>
"""


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
        self._httpd.shutdown()
        self._thread.join(timeout=5)


def start_relay(host: str = "127.0.0.1", port: int = 0) -> RelayHandle:
    """Start the HTTP server. Push frames from the Playwright thread via push_frame."""
    holder: dict[str, RelayHandle] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object, **_kwargs: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            handle = holder["h"]
            path = self.path.split("?", 1)[0]
            if path.startswith("/agent"):
                body = _AGENT_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/view"):
                handle.view_hits += 1
                ms = view_frame_ms()
                body = (
                    _VIEW_HTML.replace("__VIEW_FRAME_MS__", str(ms)).encode("utf-8")
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/status"):
                payload = json.dumps(
                    {"mode": handle.status_mode, "label": handle.status_label}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if path.startswith("/frame"):
                handle.frame_hits += 1
                with handle._lock:
                    data = handle._frame
                if not data:
                    self.send_response(204)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
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
    handle = RelayHandle(host=host, port=real_port, _httpd=httpd, _thread=thread)
    holder["h"] = handle
    thread.start()
    return handle


def start_screencast(handle: RelayHandle, page: Page):
    """Stream repaints into ``handle`` via CDP instead of polling screenshots.

    A `page.screenshot()` costs ~30ms of the Playwright thread — the same thread
    that has to keep 24kHz PCM flowing — so the old per-hop push capped motion at
    ~12fps and overran its own timing by ~1.5x. Chromium pushes screencast frames
    on repaint; we subsample via everyNthFrame to match NAVIGATOR_TARGET_FPS so
    the Cloudflare /view poll stays ahead of the encoder.

    Returns the CDP session, or None if screencast is unavailable (the caller
    then keeps using push_frame).
    """
    from navigator.core.settings import settings

    quality = max(1, min(100, int(settings.screenshot_quality or 70)))
    nth = screencast_every_nth()
    try:
        cdp = page.context.new_cdp_session(page)
    except Exception as exc:  # noqa: BLE001
        print(f"[live] screencast unavailable, using screenshots: {exc}", flush=True)
        return None

    def _on_frame(event: dict) -> None:
        data = event.get("data")
        if data:
            with handle._lock:
                handle._frame = base64.b64decode(data)
                handle.last_frame_at = time.time()
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
                "maxWidth": 1280,
                "maxHeight": 720,
                "everyNthFrame": nth,
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[live] startScreencast failed, using screenshots: {exc}", flush=True)
        return None
    print(
        f"[live] screencast=on quality={quality} everyNth={nth} "
        f"view_ms={view_frame_ms()}",
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
    try:
        data = page.screenshot(
            type="jpeg",
            quality=quality,
            clip={"x": 0, "y": 0, "width": 1280, "height": 720},
        )
    except Exception:
        data = page.screenshot(type="jpeg", quality=quality)
    with handle._lock:
        handle._frame = data
        handle.last_frame_at = time.time()
