"""Let Gemini Live drive the shared screen (show_feature) safely.

Playwright's sync API is bound to the demo thread, so Live's tool calls are
queued here and executed only when the demo thread pumps the queue (between
steps, and while it waits on speech).
"""

from __future__ import annotations

import queue
import re
import threading
import time
from typing import Any, Callable

DESTRUCTIVE = re.compile(r"log\s*-?\s*out|sign\s*-?\s*out|delete|remove|cancel subscription", re.I)

TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "show_feature",
        "description": (
            "Navigate the shared screen to a feature/menu/button the prospect asks "
            "about (e.g. 'Phonebook', 'Settings', 'Profile', 'Logout'). Clicks the "
            "visible control whose text matches. Destructive controls (logout, "
            "delete) are only highlighted, never clicked. Returns what is on screen "
            "now plus visible controls; explain the feature AFTER this returns."
        ),
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string", "description": "Visible text of the control"}},
            "required": ["target"],
        },
    },
    {
        "name": "list_screen_controls",
        "description": "List visible nav items and buttons on the shared screen right now.",
        "parameters": {"type": "object", "properties": {}},
    },
]

LIVE_TOOL_INSTRUCTION = (
    "\n\nSCREEN CONTROL: You can drive the shared screen with the show_feature tool. "
    "Whenever the prospect asks to see, find, or learn how to do something in the "
    "product, call show_feature (call list_screen_controls first if unsure, and open "
    "a parent menu such as Profile first when needed), wait for the result, then "
    "explain what is now on screen. Never say you cannot show something before "
    "trying the tool. Logout/delete are highlighted, not clicked."
)

_CONTROLS_JS = """
() => {
  const out = []; const seen = new Set();
  const els = document.querySelectorAll('a,button,[role=button],[role=menuitem],[role=tab],[role=link],nav li');
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.bottom < 0 || r.top > innerHeight) continue;
    const st = getComputedStyle(el); if (st.visibility === 'hidden' || st.display === 'none') continue;
    const t = (el.innerText || el.getAttribute('aria-label') || el.title || '').trim().replace(/\\s+/g, ' ');
    if (!t || t.length > 40 || seen.has(t.toLowerCase())) continue;
    seen.add(t.toLowerCase()); out.push(t); if (out.length >= 40) break;
  }
  return out;
}
"""


def visible_controls(page) -> list[str]:
    try:
        return list(page.evaluate(_CONTROLS_JS) or [])
    except Exception:  # noqa: BLE001
        return []


def _find(page, target: str):
    pat = re.compile(re.escape(target.strip()), re.I)
    tries = []
    for role in ("link", "button", "menuitem", "tab"):
        tries.append(page.get_by_role(role, name=pat))
    tries.append(page.get_by_text(pat))
    tries.append(page.locator(f"[aria-label*='{target}' i],[title*='{target}' i]"))
    for loc in tries:
        try:
            for i in range(min(loc.count(), 5)):
                el = loc.nth(i)
                if el.is_visible():
                    return el
        except Exception:  # noqa: BLE001
            continue
    return None


def show_feature(page, target: str, push_frame: Callable[[], None] | None = None) -> dict:
    target = (target or "").strip()
    if not target:
        return {"ok": False, "error": "empty target", "visible_controls": visible_controls(page)}
    el = _find(page, target)
    opened = ""
    if el is None:
        # Not on screen: try likely parent menus (Profile/Account/avatar/menu).
        for opener in ("Profile", "Account", "My account", "User", "Menu", "More", "Settings"):
            if opener.lower() == target.lower():
                continue
            o = _find(page, opener)
            if o is None:
                continue
            try:
                o.click(timeout=2000)
                page.wait_for_timeout(350)
            except Exception:  # noqa: BLE001
                continue
            el = _find(page, target)
            if el is not None:
                opened = opener
                break
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
    if el is None:
        return {
            "ok": False,
            "error": f"'{target}' is not visible on this screen; open its parent menu first",
            "visible_controls": visible_controls(page),
        }
    action = "clicked"
    try:
        el.scroll_into_view_if_needed(timeout=1500)
        if DESTRUCTIVE.search(target):
            el.hover(timeout=2000)
            el.evaluate("e => { e.style.outline = '3px solid #ff3b30'; e.style.outlineOffset = '2px'; }")
            action = "highlighted (not clicked: destructive)"
        else:
            el.click(timeout=4000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(400)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"could not act on '{target}': {exc}"[:200],
                "visible_controls": visible_controls(page)}
    if push_frame is not None:
        try:
            push_frame()
        except Exception:  # noqa: BLE001
            pass
    page.wait_for_timeout(150)  # viewer gets the fresh frame before Live talks
    try:
        title = page.title()
    except Exception:  # noqa: BLE001
        title = ""
    if opened:
        action = f"opened {opened} menu, then {action}"
    return {"ok": True, "action": action, "target": target, "screen_title": title,
            "url_path": re.sub(r"^https?://[^/]+", "", page.url or ""),
            "visible_controls": visible_controls(page)}


class UiJobs:
    """Thread-safe bridge: Live tool calls -> demo (Playwright) thread."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self.owner: int | None = None
        self.page = None
        self.push_frame: Callable[[], None] | None = None
        self.hold_until = 0.0

    def bind(self, page, push_frame=None) -> None:
        self.owner = threading.get_ident()
        self.page = page
        self.push_frame = push_frame

    def handle_tool(self, name: str, args: dict) -> dict:
        """Called on Live's worker thread; blocks until the demo thread runs it."""
        self.hold_until = time.monotonic() + 15.0
        key = (name, str(sorted((args or {}).items())))
        prev = getattr(self, "_recent", {}).get(key)
        if prev and time.monotonic() - prev[0] < 3.0:
            # Gemini re-issued the same call while the first was still queued.
            prev[1].wait(timeout=25.0)
            return prev[2].get("result") or {"ok": False, "error": "screen busy"}
        done = threading.Event()
        box: dict = {}
        self.__dict__.setdefault("_recent", {})[key] = (time.monotonic(), done, box)
        print(f"[ui-tool] queued {name} {args}", flush=True)
        st = getattr(self, "screen", None)
        if st is not None and st.page is not None and not st.error:
            st.submit(name, dict(args or {}), box, done)
        else:
            self._q.put((name, dict(args or {}), box, done))
        if not done.wait(timeout=30.0):
            return {"ok": False, "error": "screen busy, try again in a moment"}
        self.hold_until = time.monotonic() + 8.0
        return box.get("result") or {"ok": False}

    def pump(self) -> int:
        if self.owner is None or threading.get_ident() != self.owner or self.page is None:
            return 0
        n = 0
        while True:
            try:
                name, args, box, done = self._q.get_nowait()
            except queue.Empty:
                self._spin()
                return n
            t0 = time.monotonic()
            try:
                if name == "show_feature":
                    box["result"] = show_feature(self.page, str(args.get("target", "")), self.push_frame)
                elif name == "list_screen_controls":
                    box["result"] = {"ok": True, "visible_controls": visible_controls(self.page)}
                else:
                    box["result"] = {"ok": False, "error": f"unknown tool {name}"}
            except Exception as exc:  # noqa: BLE001
                box["result"] = {"ok": False, "error": str(exc)[:200]}
            print(f"[ui-tool] ran {name} {args} in {time.monotonic() - t0:.2f}s -> "
                  f"ok={box['result'].get('ok')} {box['result'].get('action', box['result'].get('error', ''))}",
                  flush=True)
            done.set()
            n += 1

    def _spin(self) -> None:
        """Let Playwright dispatch CDP screencast frames while we wait on speech.

        Sync Playwright only delivers events while its owner thread is inside a
        Playwright call. The demo thread blocks in say() for most of a demo, so
        without this the screenshare froze on the last frame during narration.
        """
        now = time.monotonic()
        if now - getattr(self, "_last_spin", 0.0) < 0.04:
            return
        self._last_spin = now
        try:
            self.page.wait_for_timeout(1)
        except Exception:  # noqa: BLE001
            pass

    def hold_active(self, live: Any = None) -> bool:
        now = time.monotonic()
        if now < self.hold_until:
            return True
        return bool(live is not None and getattr(live, "answering", False))


#: The running demo's job bridge (one live demo per process).
CURRENT: UiJobs | None = None


def new_current() -> UiJobs:
    global CURRENT
    CURRENT = UiJobs()
    return CURRENT


def service_before_step(deps: Any, max_hold_s: float = 30.0) -> None:
    """Demo thread: run queued screen jobs and pause the script during Q&A."""
    ui = CURRENT
    if ui is None or getattr(deps, "page", None) is None:
        return
    if ui.owner != threading.get_ident() or ui.page is not deps.page:
        ui.bind(deps.page, getattr(deps, "push_frame", None))
    ui.pump()
    live = getattr(deps, "live_agent", None)
    if not ui.hold_active(live):
        return
    t0 = time.monotonic()
    print("[flow] paused for prospect Q&A", flush=True)
    while ui.hold_active(live) and time.monotonic() - t0 < max_hold_s:
        ui.pump()
        time.sleep(0.1)
    print(f"[flow] resumed after {time.monotonic() - t0:.1f}s Q&A", flush=True)


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ScreenThread:
    """Dedicated thread with its OWN Playwright (CDP) connection to the demo
    browser. It runs the screencast and Live's screen tools independently of
    the demo thread, so frames never freeze while the demo thread blocks on
    speech/steps, and show_feature runs within ~100ms instead of waiting for
    the next step boundary.
    """

    def __init__(self, cdp_port: int, relay: Any, url_hint: str = "") -> None:
        self.port = cdp_port
        self.relay = relay
        self.url_hint = url_hint
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self.ready = threading.Event()
        self.error = ""
        self.page = None
        self._thread = threading.Thread(target=self._run, name="screen-thread", daemon=True)

    def start(self, timeout: float = 15.0) -> bool:
        self._thread.start()
        self.ready.wait(timeout)
        return self.page is not None

    def stop(self) -> None:
        self._stop.set()

    def submit(self, name: str, args: dict, box: dict, done: threading.Event) -> None:
        self._q.put((name, args, box, done))

    def _pick_page(self, browser):
        pages = [pg for ctx in browser.contexts for pg in ctx.pages]
        live = [pg for pg in pages if not pg.is_closed() and not (pg.url or "").startswith(("about:", "chrome"))]
        return (live or pages or [None])[-1]

    def _run(self) -> None:
        from playwright.sync_api import sync_playwright

        from navigator.meeting.relay import start_screencast

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
                cdp = None
                while not self._stop.is_set():
                    pg = self._pick_page(browser)
                    if pg is not None and pg is not self.page:
                        self.page = pg
                        try:
                            cdp = start_screencast(self.relay, pg) if self.relay is not None else None
                        except Exception as exc:  # noqa: BLE001
                            print(f"[screen-thread] screencast failed: {exc}", flush=True)
                        print(f"[screen-thread] attached to {pg.url} (screencast={'on' if cdp else 'off'})", flush=True)
                        self.ready.set()
                    while True:
                        try:
                            name, args, box, done = self._q.get_nowait()
                        except queue.Empty:
                            break
                        t0 = time.monotonic()
                        try:
                            if name == "show_feature":
                                box["result"] = show_feature(self.page, str(args.get("target", "")), None)
                            elif name == "list_screen_controls":
                                box["result"] = {"ok": True, "visible_controls": visible_controls(self.page)}
                            else:
                                box["result"] = {"ok": False, "error": f"unknown tool {name}"}
                        except Exception as exc:  # noqa: BLE001
                            box["result"] = {"ok": False, "error": str(exc)[:200]}
                        print(f"[ui-tool] screen-thread ran {name} {args} in {time.monotonic() - t0:.2f}s -> "
                              f"ok={box['result'].get('ok')} {box['result'].get('action', box['result'].get('error', ''))}",
                              flush=True)
                        done.set()
                    try:
                        # Keeps CDP screencast events flowing continuously.
                        (self.page or pg).wait_for_timeout(20)
                    except Exception:  # noqa: BLE001
                        time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            print(f"[screen-thread] stopped: {exc}", flush=True)
        finally:
            self.ready.set()


def highlight_text(page, target: str, timeout_ms: int = 1200) -> bool:
    """Outline (never click) the control a narration line talks about."""
    target = (target or "").strip()
    if not target or len(target) > 60:
        return False
    try:
        page.evaluate("""() => document.querySelectorAll('[data-nav-hl]').forEach(e => {
            e.style.outline = e.dataset.navHl === '-' ? '' : e.dataset.navHl; e.removeAttribute('data-nav-hl'); })""")
    except Exception:  # noqa: BLE001
        pass
    el = _find(page, target)
    if el is None:
        return False
    try:
        el.scroll_into_view_if_needed(timeout=timeout_ms)
        el.evaluate("""e => { e.dataset.navHl = e.style.outline || '-';
            e.style.outline = '3px solid #ffb020'; e.style.outlineOffset = '3px'; }""")
        el.hover(timeout=timeout_ms)  # moves the demo cursor there; hover is not a click
        return True
    except Exception:  # noqa: BLE001
        return False
