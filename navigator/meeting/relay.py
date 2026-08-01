"""Local HTTP pages that mirror a Playwright viewport for Attendee.

`/agent`  — minimal mic page for voice_agent_settings.url (bot camera tile).
`/view`   — demo frames + live status badge (Speaking / Listening / …).
`/status` — JSON status for the overlay poller.
`/frame.jpg` — latest Playwright JPEG.

ponytail: JPEG poll ~10fps. Ceiling: soft under motion. Upgrade: CDP screencast WS.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import Page

_AGENT_HTML = """<!doctype html>
<html><head><meta charset=utf-8><title>Navigator agent</title>
<style>
html,body{margin:0;width:1280px;height:720px;background:#0b1220;color:#9fb3c8;
font:600 28px system-ui,sans-serif;display:flex;align-items:center;justify-content:center}
</style></head>
<body><div>Navigator AI</div>
<script>navigator.mediaDevices.getUserMedia({audio:true}).catch(()=>{});</script>
</body></html>
"""

_VIEW_HTML = """<!doctype html>
<html><head><meta charset=utf-8><title>Navigator screen</title>
<style>
html,body{margin:0;background:#000;width:1280px;height:720px;overflow:hidden;
font-family:ui-sans-serif,system-ui,sans-serif}
img{width:1280px;height:720px;object-fit:fill;display:block;image-rendering:auto}
#badge{position:fixed;left:24px;bottom:24px;z-index:9;padding:10px 16px;
border-radius:999px;background:rgba(11,18,32,.82);color:#e8eef7;font:600 15px/1.2 system-ui;
letter-spacing:.02em;backdrop-filter:blur(6px);border:1px solid rgba(255,255,255,.12);
display:flex;align-items:center;gap:10px}
#dot{width:10px;height:10px;border-radius:50%;background:#6ee7b7;box-shadow:0 0 0 0 rgba(110,231,183,.7);
animation:pulse 1.6s infinite}
#badge[data-mode=speaking] #dot{background:#60a5fa}
#badge[data-mode=listening] #dot{background:#fbbf24}
#badge[data-mode=tailoring] #dot{background:#c084fc}
#badge[data-mode=demo] #dot{background:#6ee7b7}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(255,255,255,.35)}70%{box-shadow:0 0 0 10px rgba(255,255,255,0)}100%{box-shadow:0 0 0 0 rgba(255,255,255,0)}}
</style></head>
<body>
<img id=f alt=frame width=1280 height=720>
<div id=badge data-mode=demo><span id=dot></span><span id=label>Demo</span></div>
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
  setTimeout(tickFrame, 100);
}
async function tickStatus(){
  try {
    const r = await fetch('/status?ts='+Date.now(), {cache:'no-store'});
    if (r.ok) {
      const j = await r.json();
      const mode = (j.mode || 'demo').toLowerCase();
      const label = j.label || mode;
      const badge = document.getElementById('badge');
      badge.dataset.mode = mode;
      document.getElementById('label').textContent = label;
    }
  } catch (e) {}
  setTimeout(tickStatus, 400);
}
tickFrame();
tickStatus();
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
    status_mode: str = "demo"
    status_label: str = "Demo"

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

    def stop(self) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=5)


def start_relay(host: str = "127.0.0.1", port: int = 0) -> RelayHandle:
    """Start the HTTP server. Push frames from the Playwright thread via push_frame."""
    holder: dict[str, RelayHandle] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            handle = holder["h"]
            path = self.path.split("?", 1)[0]
            if path.startswith("/agent"):
                body = _AGENT_HTML.encode()
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

    httpd = ThreadingHTTPServer((host, port), Handler)
    real_port = int(httpd.server_address[1])
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    )
    handle = RelayHandle(host=host, port=real_port, _httpd=httpd, _thread=thread)
    holder["h"] = handle
    thread.start()
    return handle


def push_frame(handle: RelayHandle, page: Page) -> None:
    """Screenshot on the Playwright thread only (sync API is not thread-safe)."""
    try:
        data = page.screenshot(
            type="jpeg",
            quality=92,
            clip={"x": 0, "y": 0, "width": 1280, "height": 720},
        )
    except Exception:
        data = page.screenshot(type="jpeg", quality=92)
    with handle._lock:
        handle._frame = data
