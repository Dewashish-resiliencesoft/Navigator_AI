# Meet + Playwright Relay + Teams + Cursor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live path: Teams-notify Meet link → Playwright login to ResilioHub with animated cursor → tunnel a local frame-relay of that viewport → Attendee bot joins Meet streaming the relay.

**Architecture:** Stdlib HTTP Attendee + Teams clients; `FrameRelay` serves `/view` with screenshot/WebSocket frames; `cloudflared` publishes it; Attendee `voice_agent_settings.url` points at tunnel; cursor overlay in Playwright; live pytest gated on `NAVIGATOR_MEET_LIVE=1`.

**Tech Stack:** Playwright sync, stdlib `urllib`/`http.server`, cloudflared, Attendee REST, Teams Incoming Webhook.

**Spec:** `docs/superpowers/specs/2026-07-31-meet-playwright-relay-design.md`

**Commits:** Only when user asks.

**Prerequisite:** `cloudflared` not on PATH today — Task 0 installs it. User must set `.env` (Attendee cloud URL, Meet URL, product creds, Teams webhook) before live test.

---

## File map

| Path | Action |
|---|---|
| `navigator/settings.py` | Add new env fields |
| `.env.example` | Placeholders |
| `navigator/meeting/attendee.py` | Implement join/get/leave |
| `navigator/meeting/teams.py` | Create |
| `navigator/meeting/relay.py` | Create |
| `navigator/meeting/tunnel.py` | Create |
| `navigator/meeting/live_demo.py` | Create — orchestration |
| `navigator/browser/cursor.py` | Create |
| `navigator/browser/product_login.py` | Create |
| `navigator/browser/tools.py` | Optional cursor hook via callback |
| `navigator/agent/state.py` | Optional `meeting_url` / `attendee` on deps |
| `tests/test_attendee.py` | Create |
| `tests/test_teams.py` | Create |
| `tests/test_cursor.py` | Create |
| `tests/test_relay.py` | Create |
| `tests/test_meet_demo.py` | Create — live gated |

---

### Task 0: Install cloudflared

- [ ] **Step 1: Install cloudflared**

```bash
# Prefer official binary; adjust if already present
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /tmp/cloudflared
chmod +x /tmp/cloudflared
sudo mv /tmp/cloudflared /usr/local/bin/cloudflared || mv /tmp/cloudflared "$HOME/.local/bin/cloudflared"
cloudflared --version
```

Expected: version prints. If no sudo and no `~/.local/bin` on PATH, put binary in repo `.tools/cloudflared` and set `NAVIGATOR_TUNNEL_BIN` to that path (add `.tools/` to `.gitignore`).

- [ ] **Step 2: Confirm**

Run: `command -v cloudflared || command -v "$HOME/.local/bin/cloudflared"`

---

### Task 1: Settings + `.env.example`

**Files:**
- Modify: `navigator/settings.py`
- Modify: `.env.example`

- [ ] **Step 1: Extend Settings**

Add to `Settings` (after attendee fields):

```python
    meeting_url: str = ""
    product_url: str = ""
    product_login_email: str = ""
    product_login_password: str = ""
    teams_webhook_url: str = ""
    tunnel_bin: str = "cloudflared"
    meet_live: bool = False
```

- [ ] **Step 2: Update `.env.example`**

Append placeholders (empty values):

```bash
# Phase 3: Meet + Teams + product login (never commit real secrets)
NAVIGATOR_ATTENDEE_BASE_URL=https://app.attendee.dev/api/v1
NAVIGATOR_MEETING_URL=
NAVIGATOR_PRODUCT_URL=
NAVIGATOR_PRODUCT_LOGIN_EMAIL=
NAVIGATOR_PRODUCT_LOGIN_PASSWORD=
NAVIGATOR_TEAMS_WEBHOOK_URL=
NAVIGATOR_TUNNEL_BIN=cloudflared
NAVIGATOR_MEET_LIVE=0
```

- [ ] **Step 3: Patch local `.env` Attendee URL** (do not print secrets)

Ensure `NAVIGATOR_ATTENDEE_BASE_URL=https://app.attendee.dev/api/v1` (replace localhost). Add other keys if missing — user supplies values.

---

### Task 2: Attendee client (join/get/leave)

**Files:**
- Modify: `navigator/meeting/attendee.py`
- Create: `tests/test_attendee.py`

- [ ] **Step 1: Write failing tests**

```python
"""Attendee REST client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navigator.meeting.attendee import AttendeeClient, Bot


def test_join_posts_bot_and_voice_agent_url():
    client = AttendeeClient("https://app.attendee.dev/api/v1", "tok")
    fake_resp = MagicMock()
    fake_resp.status = 201
    fake_resp.read.return_value = b'{"id":"bot_1","state":"joining"}'
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)

    with patch("navigator.meeting.attendee.urlopen", return_value=fake_resp) as open_:
        with patch("navigator.meeting.attendee.Request") as Req:
            bot = client.join(
                "https://meet.google.com/x",
                bot_name="Navigator AI",
                voice_agent_url="https://tunnel.example/view",
            )
    assert bot.id == "bot_1"
    assert bot.state == "joining"
    kwargs = Req.call_args
    # body must include voice_agent_settings.url
    import json
    body = json.loads(kwargs[1]["data"].decode() if "data" in kwargs[1] else kwargs[0][0].data.decode())
    # Flexible: inspect Request positional
    req = Req.call_args[0][0] if Req.call_args[0] else Req.call_args[1].get("url")
    # Simpler assert via capturing data in a custom side effect — implementer may
    # refine mock to assert JSON once Request wiring is known.


def test_get_maps_state():
    client = AttendeeClient("https://app.attendee.dev/api/v1", "tok")
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.read.return_value = b'{"id":"bot_1","state":"joined"}'
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)
    with patch("navigator.meeting.attendee.urlopen", return_value=fake_resp):
        with patch("navigator.meeting.attendee.Request"):
            bot = client.get("bot_1")
    assert bot.state == "joined"


def test_leave_posts():
    client = AttendeeClient("https://app.attendee.dev/api/v1", "tok")
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.read.return_value = b"{}"
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)
    with patch("navigator.meeting.attendee.urlopen", return_value=fake_resp):
        with patch("navigator.meeting.attendee.Request"):
            client.leave("bot_1")  # no raise
```

Implementer: tighten mocks to capture request body with a list append side_effect on `Request`.

- [ ] **Step 2: Run — expect fail / NotImplemented**

Run: `.venv/bin/python -m pytest tests/test_attendee.py -v`

- [ ] **Step 3: Implement client**

```python
"""Attendee API client -- meeting bot for Zoom / Google Meet."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BotState = Literal["joining", "joined", "leaving", "ended", "fatal_error"]

_STATE_MAP = {
    "ready": "joining",
    "joining": "joining",
    "joined_not_recording": "joined",
    "joined_recording": "joined",
    "joined": "joined",
    "leaving": "leaving",
    "post_processing": "ended",
    "ended": "ended",
    "fatal_error": "fatal_error",
    "waiting_room": "joining",
}


@dataclass
class Bot:
    id: str
    state: BotState


class AttendeeClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = None if body is None else json.dumps(body).encode()
        req = Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=60) as resp:
                raw = resp.read() or b"{}"
        except HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise RuntimeError(f"Attendee {method} {path} -> {e.code}: {detail}") from e
        except URLError as e:
            raise RuntimeError(f"Attendee unreachable: {e}") from e
        return json.loads(raw) if raw else {}

    def join(
        self,
        meeting_url: str,
        bot_name: str = "Navigator AI",
        voice_agent_url: str | None = None,
    ) -> Bot:
        payload: dict = {"meeting_url": meeting_url, "bot_name": bot_name}
        if voice_agent_url:
            payload["voice_agent_settings"] = {"url": voice_agent_url}
        data = self._request("POST", "/bots", payload)
        return self._bot(data)

    def get(self, bot_id: str) -> Bot:
        return self._bot(self._request("GET", f"/bots/{bot_id}"))

    def leave(self, bot_id: str) -> None:
        self._request("POST", f"/bots/{bot_id}/leave", {})

    def speak(self, bot_id: str, wav: bytes) -> None:
        raise NotImplementedError("speak lands with Piper→Meet wiring")

    def audio_stream(self, bot_id: str):
        raise NotImplementedError("audio_stream lands with STT")

    def send_video(self, bot_id: str, device: str) -> None:
        raise NotImplementedError("send_video unused; relay uses voice_agent_settings.url")

    @staticmethod
    def _bot(data: dict) -> Bot:
        raw = str(data.get("state", "joining"))
        state: BotState = _STATE_MAP.get(raw, "joining")  # type: ignore[assignment]
        if raw in ("fatal_error",) or state == "fatal_error":
            state = "fatal_error"
        elif raw not in _STATE_MAP:
            # Unknown — keep as joining unless clearly ended
            state = "joined" if "joined" in raw else "joining"
        return Bot(id=str(data["id"]), state=state)
```

Refine `_STATE_MAP` against a live `GET` response during Task 8 if states differ.

- [ ] **Step 4: Tests pass**

Run: `.venv/bin/python -m pytest tests/test_attendee.py -v`

---

### Task 3: Teams webhook

**Files:**
- Create: `navigator/meeting/teams.py`
- Create: `tests/test_teams.py`

- [ ] **Step 1: Test**

```python
from unittest.mock import patch, MagicMock
from navigator.meeting.teams import notify_demo_link

def test_notify_posts_text():
    captured = {}
    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["body"] = req.data
        m = MagicMock()
        m.status = 200
        m.read.return_value = b"1"
        m.__enter__ = lambda s: s
        m.__exit__ = MagicMock(return_value=False)
        return m
    with patch("navigator.meeting.teams.urlopen", side_effect=fake_urlopen):
        notify_demo_link(
            webhook_url="https://example.webhook",
            meeting_url="https://meet.google.com/abc",
        )
    import json
    assert json.loads(captured["body"])["text"].startswith("Navigator demo")
    assert "meet.google.com/abc" in json.loads(captured["body"])["text"]
```

- [ ] **Step 2: Implement**

```python
"""Teams Incoming Webhook notifier."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def notify_demo_link(
    *,
    webhook_url: str,
    meeting_url: str,
    message: str | None = None,
) -> None:
    text = message or f"Navigator demo starting — join: {meeting_url}"
    if meeting_url not in text:
        text = f"{text}\n{meeting_url}"
    req = Request(
        webhook_url,
        data=json.dumps({"text": text}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=30) as resp:
            if getattr(resp, "status", 200) >= 300:
                raise RuntimeError(f"Teams webhook HTTP {resp.status}")
    except HTTPError as e:
        raise RuntimeError(f"Teams webhook failed: {e.code} {e.read()}") from e
    except URLError as e:
        raise RuntimeError(f"Teams webhook unreachable: {e}") from e
```

- [ ] **Step 3: pytest tests/test_teams.py -v**

---

### Task 4: Cursor overlay

**Files:**
- Create: `navigator/browser/cursor.py`
- Create: `tests/test_cursor.py`
- Modify: `navigator/browser/tools.py` (optional hook)

- [ ] **Step 1: Test install**

```python
def test_install_cursor_adds_overlay(page):
    from navigator.browser.cursor import install_cursor
    # page fixture is already on inbox; any page works
    install_cursor(page)
    assert page.locator("#nav-cursor").count() == 1
```

Use existing `page` fixture from conftest or create blank page.

- [ ] **Step 2: Implement cursor.py**

```python
"""Visible cursor overlay for demos (CSS, not OS pointer)."""

from __future__ import annotations

import time

from playwright.sync_api import Page

_CURSOR_JS = """
(() => {
  if (document.getElementById('nav-cursor')) return;
  const c = document.createElement('div');
  c.id = 'nav-cursor';
  c.style.cssText = 'position:fixed;left:0;top:0;width:18px;height:18px;border-radius:50%;border:2px solid #0a5c31;background:rgba(10,92,49,0.35);pointer-events:none;z-index:2147483647;transform:translate(-50%,-50%);transition:left 80ms linear, top 80ms linear;';
  document.documentElement.appendChild(c);
  const r = document.createElement('div');
  r.id = 'nav-cursor-ripple';
  r.style.cssText = 'position:fixed;left:0;top:0;width:8px;height:8px;border-radius:50%;border:2px solid #0a5c31;pointer-events:none;z-index:2147483647;transform:translate(-50%,-50%) scale(0);opacity:0;';
  document.documentElement.appendChild(r);
})();
"""


def install_cursor(page: Page) -> None:
    page.add_init_script(_CURSOR_JS)
    page.evaluate(_CURSOR_JS)


def move_cursor(page: Page, x: float, y: float, steps: int = 8) -> None:
    install_cursor(page)
    page.evaluate(
        """([x, y, steps]) => {
          const c = document.getElementById('nav-cursor');
          if (!c) return;
          const x0 = parseFloat(c.style.left) || 0;
          const y0 = parseFloat(c.style.top) || 0;
          for (let i = 1; i <= steps; i++) {
            const t = i / steps;
            c.style.left = (x0 + (x - x0) * t) + 'px';
            c.style.top = (y0 + (y - y0) * t) + 'px';
          }
        }""",
        [x, y, steps],
    )
    time.sleep(0.08 * max(steps, 1) / 8)


def click_with_cursor(page: Page, selector: str, timeout: float = 5000) -> None:
    loc = page.locator(selector).first
    box = loc.bounding_box(timeout=timeout)
    if box is None:
        raise RuntimeError(f"no box for {selector}")
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    move_cursor(page, x, y)
    page.evaluate(
        """([x, y]) => {
          const r = document.getElementById('nav-cursor-ripple');
          if (!r) return;
          r.style.left = x + 'px'; r.style.top = y + 'px';
          r.style.transition = 'transform 300ms ease-out, opacity 300ms ease-out';
          r.style.transform = 'translate(-50%,-50%) scale(4)';
          r.style.opacity = '0.6';
          setTimeout(() => { r.style.opacity = '0'; r.style.transform = 'translate(-50%,-50%) scale(0)'; }, 320);
        }""",
        [x, y],
    )
    loc.click(timeout=timeout)
```

- [ ] **Step 3: Hook tools (minimal)**

In `click_element` / `fill_field`, if `page.evaluate("!!document.getElementById('nav-cursor')")`, call `move_cursor` toward target before action. Or only use `click_with_cursor` from `live_demo` / `product_login` to avoid changing tool semantics — **prefer live_demo + product_login only** (ponytail: fewer files).

- [ ] **Step 4: pytest tests/test_cursor.py -v**

---

### Task 5: Product login

**Files:**
- Create: `navigator/browser/product_login.py`
- Create: unit test that mocks page OR live-only check in meet_demo

- [ ] **Step 1: Implement login helper**

Inspect ResilioHub login DOM once with a one-off script (or guess common patterns). Prefer env overrides:

```python
"""Log into a hosted product for live demos."""

from __future__ import annotations

from playwright.sync_api import Page

from navigator.browser.cursor import click_with_cursor, install_cursor, move_cursor


def login_product(
    page: Page,
    *,
    url: str,
    email: str,
    password: str,
    email_selector: str = 'input[type="email"], input[name="email"], #email',
    password_selector: str = 'input[type="password"], input[name="password"], #password',
    submit_selector: str = 'button[type="submit"], button:has-text("Log"), button:has-text("Sign")',
    ready_selector: str | None = None,
) -> None:
    install_cursor(page)
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(500)
    # If already on dashboard (session), return
    if ready_selector and page.locator(ready_selector).count():
        return
    page.locator(email_selector).first.fill(email, timeout=30_000)
    page.locator(password_selector).first.fill(password, timeout=15_000)
    click_with_cursor(page, submit_selector, timeout=15_000)
    if ready_selector:
        page.wait_for_selector(ready_selector, timeout=60_000)
    else:
        page.wait_for_load_state("networkidle", timeout=60_000)
```

Tune selectors after first live probe in Task 8.

---

### Task 6: Frame relay

**Files:**
- Create: `navigator/meeting/relay.py`
- Create: `tests/test_relay.py`

- [ ] **Step 1: Implement screenshot-loop relay** (ponytail ceiling: ~5 fps; upgrade CDP)

```python
"""Local HTTP page that mirrors a Playwright page for Attendee voice_agent_settings.url."""

from __future__ import annotations

import base64
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import Page

_VIEW_HTML = """<!doctype html>
<html><head><meta charset=utf-8><title>Navigator relay</title>
<style>html,body{margin:0;background:#111;width:1280px;height:720px;overflow:hidden}
img{width:1280px;height:720px;object-fit:contain;display:block}</style></head>
<body><img id=f alt=frame>
<script>
navigator.mediaDevices.getUserMedia({audio:true}).catch(()=>{});
async function tick(){
  try {
    const r = await fetch('/frame.jpg?ts='+Date.now());
    if (r.ok) {
      const b = await r.blob();
      document.getElementById('f').src = URL.createObjectURL(b);
    }
  } catch(e) {}
  setTimeout(tick, 200);
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
    _stop: threading.Event
    _frame: bytes
    _lock: threading.Lock

    @property
    def view_url(self) -> str:
        return f"http://{self.host}:{self.port}/view"

    def stop(self) -> None:
        self._stop.set()
        self._httpd.shutdown()
        self._thread.join(timeout=5)


def start_relay(page: Page, host: str = "127.0.0.1", port: int = 0) -> RelayHandle:
    stop = threading.Event()
    lock = threading.Lock()
    frame = b""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: ANN002
            return

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/view"):
                body = _VIEW_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/frame"):
                with lock:
                    data = frame
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
    real_port = httpd.server_address[1]

    def serve() -> None:
        httpd.serve_forever(poll_interval=0.2)

    def capture() -> None:
        nonlocal frame
        while not stop.is_set():
            try:
                # Playwright sync API is not thread-safe — capture on main thread.
                pass
            except Exception:
                pass
            stop.wait(0.2)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    handle = RelayHandle(host, real_port, httpd, t, stop, frame, lock)
    return handle


def push_frame(handle: RelayHandle, page: Page) -> None:
    """Call from the Playwright thread between actions / on a timer."""
    data = page.screenshot(type="jpeg", quality=60)
    with handle._lock:
        handle._frame = data
```

**Important:** Do not call `page.screenshot` from the HTTP thread. Live demo loop: `while running: push_frame(handle, page); sleep(0.2)` on main thread alongside actions, or push after each cursor action.

- [ ] **Step 2: test_relay** — start relay without page frames; GET `/view` → 200

```python
def test_view_returns_html():
    from navigator.meeting.relay import start_relay_http_only  # or start with None page
    ...
```

Refactor `start_relay` to allow HTTP-only start for unit test; `push_frame` optional.

---

### Task 7: Tunnel

**Files:**
- Create: `navigator/meeting/tunnel.py`
- Optional unit test with fake binary script

```python
"""Publish a local port via cloudflared quick tunnel."""

from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass


@dataclass
class TunnelHandle:
    public_url: str
    _proc: subprocess.Popen

    def stop(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()


_URL_RE = re.compile(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com")


def start_tunnel(local_port: int, binary: str = "cloudflared") -> TunnelHandle:
    proc = subprocess.Popen(
        [binary, "tunnel", "--url", f"http://127.0.0.1:{local_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    public = None
    deadline = time.time() + 45
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        m = _URL_RE.search(line or "")
        if m:
            public = m.group(0)
            break
    if not public:
        proc.kill()
        raise RuntimeError(
            f"tunnel did not publish a URL (is {binary!r} installed?)"
        )
    return TunnelHandle(public_url=public, _proc=proc)
```

---

### Task 8: Live orchestration + gated test

**Files:**
- Create: `navigator/meeting/live_demo.py`
- Create: `tests/test_meet_demo.py`

- [ ] **Step 1: `run_live_meet_smoke(...)`**

Orchestrate: Teams notify → browser login → relay → tunnel → Attendee join → poll joined → push frames + a few cursor moves for ~30s → leave → cleanup. Always cleanup in `finally`.

- [ ] **Step 2: Live test**

```python
import os
import pytest
from navigator.settings import settings

pytestmark = pytest.mark.skipif(
    not settings.meet_live,
    reason="set NAVIGATOR_MEET_LIVE=1 for live Meet test",
)

def test_bot_joins_meet_and_teams_notified():
    missing = [
        n for n, v in [
            ("ATTENDEE_API_KEY", settings.attendee_api_key),
            ("MEETING_URL", settings.meeting_url),
            ("PRODUCT_URL", settings.product_url),
            ("PRODUCT_LOGIN_EMAIL", settings.product_login_email),
            ("PRODUCT_LOGIN_PASSWORD", settings.product_login_password),
            ("TEAMS_WEBHOOK_URL", settings.teams_webhook_url),
        ]
        if not v
    ]
    if "localhost" in settings.attendee_base_url:
        pytest.fail("NAVIGATOR_ATTENDEE_BASE_URL still points at localhost")
    if missing:
        pytest.fail(f"missing env for live test: {missing}")
    from navigator.meeting.live_demo import run_live_meet_smoke
    run_live_meet_smoke()
```

- [ ] **Step 3: Default pytest still skips live test; full suite green**

Run: `.venv/bin/python -m pytest -q`  
Expected: live skipped; all else pass.

- [ ] **Step 4: Live run (user present on Meet + Teams)**

```bash
# after .env filled
NAVIGATOR_MEET_LIVE=1 .venv/bin/python -m pytest tests/test_meet_demo.py -v -s
```

Tune login selectors if login fails; dump screenshot to `/tmp/nav-login-fail.png`.

---

### Task 9: Wire joining node lightly + docs check

- [ ] Update `joining.py`: if `getattr(deps, "meeting_url", None)` and attendee on deps, call join+poll; else keep standalone message.
- [ ] Extend `CallDeps` with `meeting_url`, `attendee: AttendeeClient | None = None` as needed by runner.
- [ ] `.venv/bin/python -m navigator.docs check` — expect up to date unless OpenAPI changed.
- [ ] Full `pytest -q`.

---

## Spec coverage

| Spec item | Task |
|---|---|
| Attendee join/get/leave | 2 |
| Teams webhook | 3 |
| Cursor | 4 |
| Product login | 5 |
| Frame relay | 6 |
| Tunnel | 7 |
| Live orchestration + gated test | 8 |
| Settings / env example | 1 |
| cloudflared install | 0 |
| No secrets in git | 1, 8 |

## Risk notes

- Attendee bot state enums may need live calibration.
- ResilioHub login selectors unknown until first probe.
- Playwright page is not thread-safe — frame capture on main thread only.
- cloudflared quick tunnels need network; corporate firewall may block.
