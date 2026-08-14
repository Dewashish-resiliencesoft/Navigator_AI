"""Local HTTP pages that mirror a Playwright viewport for Attendee.

`/agent`  — blank mic page for Attendee camera tile (no 3D avatar).
`/view`   — demo frames only (no status pill).
`/status` — JSON status (kept for API; /view no longer polls it).
`/frame.jpg` — latest Playwright JPEG.

ponytail: frames come from CDP Page.screencast (see start_screencast) — Chromium
pushes a JPEG whenever the page repaints, so in-page CSS animation reaches Meet
at ~60fps and an idle page costs nothing. push_frame stays for seeding the first
paint and for demos with no screencast attached.
"""

from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import Page

#: Meet `/view` JPEG poll cadence. 30fps looks smooth without the JPEG spam that
#: froze Chromium at 60fps. Viable now that Attendee's ffmpeg recorder is off.
#: ponytail: 30fps ceiling. Bump toward 16ms only if the box has headroom.
VIEW_FRAME_MS = 33
#: CDP screencast every repaint (60fps source) — cursor motion stays fluid.
SCREENCAST_EVERY_NTH_FRAME = 1


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
    const r = await fetch('/frame.jpg?ts='+Date.now(), {cache:'no-store'});
    if (r.ok) {
      const b = await r.blob();
      const url = URL.createObjectURL(b);
      const img = document.getElementById('f');
      const old = img.src;
      img.src = url;
      if (old && old.startsWith('blob:')) URL.revokeObjectURL(old);
    }
  } catch (e) {}
  setTimeout(tickFrame, 33);
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
                body = _VIEW_HTML.encode()
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
    on repaint for free, so an in-page CSS animation arrives at ~60fps.

    Returns the CDP session, or None if screencast is unavailable (the caller
    then keeps using push_frame).
    """
    from navigator.core.settings import settings

    quality = max(1, min(100, int(settings.screenshot_quality or 70)))
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
                "everyNthFrame": SCREENCAST_EVERY_NTH_FRAME,
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[live] startScreencast failed, using screenshots: {exc}", flush=True)
        return None
    print(f"[live] screencast=on quality={quality}", flush=True)
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
