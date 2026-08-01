"""Local HTTP pages that mirror a Playwright viewport for Attendee.

`/agent`  — minimal mic page for voice_agent_settings.url (bot camera tile).
`/view`   — demo frames for voice_agent_settings.screenshare_url (Meet screen share).

ponytail: JPEG poll ~10fps. Ceiling: soft under motion. Upgrade: CDP screencast WS.
"""

from __future__ import annotations

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
html,body{margin:0;background:#000;width:1280px;height:720px;overflow:hidden}
img{width:1280px;height:720px;object-fit:fill;display:block;image-rendering:auto}
</style></head>
<body><img id=f alt=frame width=1280 height=720>
<script>
async function tick(){
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
  setTimeout(tick, 100);
}
tick();
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

    @property
    def view_url(self) -> str:
        return f"http://{self.host}:{self.port}/view"

    @property
    def agent_url(self) -> str:
        return f"http://{self.host}:{self.port}/agent"

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
            if self.path.startswith("/agent"):
                body = _AGENT_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/view"):
                body = _VIEW_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/frame"):
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
