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
            "now plus visible controls; explain the feature AFTER this returns. "
            "Only say which page we are on from the returned url_path/screen_title; "
            "if ok is false, say you could not open it. Never guess the current page."
        ),
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string", "description": "Visible text of the control"}},
            "required": ["target"],
        },
    },
    {
        "name": "do_task",
        "description": (
            "Do or demonstrate a task on the shared screen when the prospect asks, e.g. 'how do I "
            "create a contact' (mode=explain: opens the form and lights up each field; explain each "
            "field in order, never submit) or 'create a contact, name X, phone Y' (mode=do: types the "
            "details visibly). Say a short 'Sure, let me do that' BEFORE calling. In do mode the result "
            "may be status=need_details (ask for the missing fields by voice, then call again with all "
            "details), or status=awaiting_confirmation (read the details back and ask 'shall I save "
            "it?'; on yes call confirm_submit). Set confirmed=true only if the prospect already said to "
            "save/submit. Deleting, sending messages/campaigns, payments, passwords, API keys and "
            "account settings are refused: show and explain only. Report only what the result says."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The task in the prospect's words, e.g. 'create a contact'"},
                "mode": {"type": "string", "enum": ["explain", "do"]},
                "details": {"type": "string", "description": "Values to type, as 'field: value; field: value'"},
                "confirmed": {"type": "boolean"},
            },
            "required": ["goal", "mode"],
        },
    },
    {
        "name": "confirm_submit",
        "description": "Press the save/create button of the form do_task filled, after the prospect said yes. Returns whether it saved (checked on screen).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "fill_field",
        "description": "Type a value into one visible form field (by its label), e.g. to correct a detail.",
        "parameters": {"type": "object", "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
                       "required": ["label", "value"]},
    },
    {
        "name": "click_control",
        "description": "Click a non-destructive visible control (tab, 'Create', 'Manual entry') or open a list entry: 'first chat', 'last contact', or a person's/item's name. Never for save/delete/send.",
        "parameters": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
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
    "trying the tool. Logout/delete are highlighted, not clicked. For 'how do I X' or 'do X for me' "
    "use do_task (explain or do). To open an entry in a list (a chat in the Inbox, a contact, a row) call "
    "click_control with e.g. 'first chat' or the name: never ask the prospect to click anything and never "
    "say you cannot see the screen. Tools are only for the prospect's requests, never on your own "
    "while reading the script."
    "\nACK FIRST: before any screen tool call, say a 2-5 word acknowledgement "
    "('Sure, opening it now.') so the prospect never waits in silence. If a tool "
    "fails or times out, say so honestly in one sentence and offer to explain instead."
    "\nACT, DON'T INSTRUCT: when the prospect says 'show me', 'how do I', 'how to', 'can you', "
    "'create', 'add' or 'open', your FIRST response is the tool call (do_task with mode 'explain' "
    "for how-to, mode 'do' when they gave details or asked you to do it) - right after the short ack. Never "
    "answer with step-by-step instructions like 'first click X, then click Y' and never ask which "
    "option they want: pick the single/manual option (e.g. Create Contact > Manual Entry) and do it "
    "visibly, then describe what is on screen in one or two sentences. Call each tool once per "
    "request; do not repeat a call that is still running."
    "\nHONESTY: never say you are filling, opening, clicking, showing or are 'already on' a "
    "screen unless a tool result in THIS turn confirms it; you only know the screen from tool "
    "results and context notes. Never invent details (names, numbers, emails): ask the prospect "
    "for them. Never push the prospect to move on; stay on their question until they are done."
)

#: URL the scripted flow was on before a prospect's request moved the screen;
#: the flow executor returns there before its next step.
DETOUR_FROM: list[str] = []
#: Explain-mode field tours waiting to be paced on the screen thread.
DEFERRED_TOURS: list = []


def dispatch(page, name: str, args: dict, push_frame=None) -> dict:
    """One place that runs a Live screen tool (screen thread or demo thread)."""
    from navigator.meeting import task_runner as tr

    url0 = getattr(page, "url", "") or ""
    if name == "show_feature":
        res = show_feature(page, str(args.get("target", "")), push_frame)
    elif name == "list_screen_controls":
        res = {"ok": True, "visible_controls": visible_controls(page)}
    elif name == "do_task":
        res = tr.do_task(page, str(args.get("goal", "")), args.get("details", ""),
                         str(args.get("mode", "explain")), bool(args.get("confirmed", False)), push_frame)
        if res.get("tour"):
            DEFERRED_TOURS.append((time.monotonic(), res.pop("tour")))
    elif name == "confirm_submit":
        res = tr.confirm_submit(page, push_frame)
    elif name == "fill_field":
        res = tr.fill_field(page, str(args.get("label", "")), str(args.get("value", "")), push_frame)
    elif name == "click_control":
        res = tr.click_control(page, str(args.get("label", "")), push_frame)
    else:
        res = {"ok": False, "error": f"unknown tool {name}"}
    try:
        if (page.url or "") != url0 and not DETOUR_FROM:
            DETOUR_FROM.append(url0)
    except Exception:  # noqa: BLE001
        pass
    return res


def run_deferred_tours(page, push_frame=None) -> None:
    """Pace explain-mode field highlights to Live's narration (~2.2s per field)."""
    if not DEFERRED_TOURS:
        return
    from navigator.meeting.task_runner import _outline

    t_req, ids = DEFERRED_TOURS[0]
    now = time.monotonic()
    i = int((now - t_req - 1.5) // 2.2)
    if i < 0:
        return
    if i >= len(ids):
        DEFERRED_TOURS.pop(0)
        return
    key = (t_req, i)
    if getattr(run_deferred_tours, "_last", None) == key:
        return
    run_deferred_tours._last = key
    try:
        _outline(page, page.locator(f"[data-nav-field='{ids[i]}']").first, "#2f80ed")
        if push_frame is not None:
            push_frame()
    except Exception:  # noqa: BLE001
        pass

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


_IN_MENU_JS = """e => { for (let a = e.parentElement; a; a = a.parentElement) {
  if (a.matches('aside, nav, [class*=sidebar i], [class*=sidenav i]') && a.getBoundingClientRect().width < 420) return true; }
  return false; }"""


def _find_main(page, target: str):
    """Like _find, but prefer a match in the page content over the side menu
    (the tour points at the screen's own heading/tab, not the nav link)."""
    pat = re.compile(r"^\s*" + re.escape(target.strip()) + r"\s*$", re.I)
    loose = re.compile(re.escape(target.strip()), re.I)
    fallback = None
    for p in (pat, loose):
        tries = [page.get_by_role(r, name=p) for r in ("tab", "button", "heading", "columnheader", "link")]
        tries.append(page.get_by_text(p))
        for loc in tries:
            try:
                for i in range(min(loc.count(), 6)):
                    el = loc.nth(i)
                    if not el.is_visible():
                        continue
                    if not el.evaluate(_IN_MENU_JS):
                        return el
                    fallback = fallback or el
            except Exception:  # noqa: BLE001
                continue
    return fallback


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


NAV_INVENTORY: list = []  # set per demo from _meta.nav_inventory (label/href/group)


def _squash(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def _hidden_link_href(page, target: str) -> str:
    """Same-origin href for ``target``: the crawled nav inventory first (links in
    collapsed menu groups are not in the DOM), then any hidden link in the page."""
    want = _squash(target)
    if want:
        from urllib.parse import urljoin

        for row in NAV_INVENTORY or []:
            if _squash(str(row.get("label", ""))) == want and str(row.get("href", "")).startswith("/"):
                return urljoin(page.url, row["href"])
    try:
        return page.evaluate("""(t) => {
          const want = t.trim().toLowerCase();
          let best = '';
          for (const a of document.querySelectorAll('a[href]')) {
            const txt = (a.textContent || a.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').toLowerCase();
            if (!txt) continue;
            const u = new URL(a.getAttribute('href'), location.href);
            if (u.origin !== location.origin) continue;
            if (txt === want) return u.href;
            if (!best && txt.includes(want) && txt.length <= want.length + 12) best = u.href;
          }
          return best;
        }""", target) or ""
    except Exception:  # noqa: BLE001
        return ""


_SEQ: dict[str, int] = {}
#: tools where only the newest request matters (screen moves)
_LATEST_WINS = {"show_feature", "click_control"}


def _next_seq(name: str) -> int:
    _SEQ[name] = _SEQ.get(name, 0) + 1
    return _SEQ[name]


def superseded(name: str, box: dict) -> dict | None:
    """A newer show_feature arrived while this one waited behind a slow job:
    running both just flips the screen twice. Skip the stale one."""
    if name in _LATEST_WINS and int(box.get("seq", 0)) < _SEQ.get(name, 0):
        return {"ok": False, "error": "superseded by a newer request", "skipped": True}
    return None


def _close_blocking_dialog(page, el) -> None:
    try:
        dlg = page.locator("[role=dialog],[aria-modal=true],dialog[open]")
        if dlg.count() and dlg.last.is_visible() and not el.evaluate(
                "e => !!e.closest('[role=dialog],[aria-modal=true],dialog')"):
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
    except Exception:  # noqa: BLE001
        pass


#: label -> known path, from the crawl's nav inventory and the flow's pages.
SCREEN_MAP: dict[str, str] = {}
SHOW_BUDGET_S = 8.0


def build_screen_map(graph) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in NAV_INVENTORY or []:
        lab, href = str(e.get("label") or "").strip(), str(e.get("href") or "")
        if lab and href.startswith("/"):
            out.setdefault(lab, href)
    for pg in (getattr(graph, "pages", None) or {}).values():
        name, url = str(getattr(pg, "name", "") or "").strip(), str(getattr(pg, "url", "") or "")
        if name and url.startswith("/") and not any(c in url for c in "*{}<>"):
            out.setdefault(name, url)
    return out


def resolve_screen(target: str, screens: dict[str, str] | None = None, strict: bool = False) -> tuple[str, str]:
    """Fuzzy-match a spoken name to a known screen ('Local Seat' -> 'Local Sheets').
    Returns (label, path) or ("", "")."""
    import difflib

    screens = SCREEN_MAP if screens is None else screens
    t = " ".join((target or "").lower().split())
    if not t or not screens:
        return "", ""
    by_low = {k.lower(): k for k in screens}
    if t in by_low:
        return by_low[t], screens[by_low[t]]
    best = difflib.get_close_matches(t, list(by_low), n=1, cutoff=0.72)
    if not best and not strict:
        sub = [k for k in by_low if t in k or k in t]
        best = sorted(sub, key=len, reverse=True)[:1]
    if not best:
        return "", ""
    lab = by_low[best[0]]
    return lab, screens[lab]


def _fast_goto(page, path: str, push_frame) -> dict | None:
    from urllib.parse import urljoin

    try:
        page.goto(urljoin(page.url, path), wait_until="commit", timeout=int(SHOW_BUDGET_S * 1000))
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:  # noqa: BLE001
            pass  # SPA still hydrating: the screen is already changing; good enough
        page.wait_for_timeout(300)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"could not open {path}: {str(exc).splitlines()[0][:120]}"}
    if push_frame is not None:
        try:
            push_frame()
        except Exception:  # noqa: BLE001
            pass
    return None


def show_feature(page, target: str, push_frame: Callable[[], None] | None = None) -> dict:
    target = (target or "").strip()
    if not target:
        return {"ok": False, "error": "empty target", "visible_controls": visible_controls(page)}
    # Known screen (crawl/flow map, fuzzy name): go straight there — bounded,
    # never a guessed URL. 16:23 run: 'Integrations' searched for 34s and
    # 'Local Sheets' 68s before failing.
    lab, path = resolve_screen(target)
    if path and not DESTRUCTIVE.search(lab):
        from urllib.parse import urlparse

        cur = (urlparse(page.url or "").path or "/").rstrip("/")
        if cur != path.rstrip("/"):
            err = _fast_goto(page, path, push_frame)
            if err:
                return {**err, "target": lab}
        return {"ok": True, "action": "opened", "target": lab, "screen_title": page.title(),
                "url_path": re.sub(r"^https?://[^/]+", "", page.url or ""),
                "visible_controls": visible_controls(page)}
    t_start = time.monotonic()
    el = _find(page, target)
    opened = ""
    if el is None:
        # Not on screen: try likely parent menus (Profile/Account/avatar/menu).
        for opener in ("Profile", "Account", "My account", "User", "Menu", "More", "Settings"):
            if time.monotonic() - t_start > SHOW_BUDGET_S:
                break
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
    if el is None and not DESTRUCTIVE.search(target):
        # Collapsed menu group (16:21: Phonebook sat under a closed "Overview"):
        # the link exists in the DOM — go to its href directly.
        href = _hidden_link_href(page, target)
        if href:
            try:
                page.goto(href, wait_until="domcontentloaded", timeout=8000)
                page.wait_for_timeout(600)
                if push_frame is not None:
                    try:
                        push_frame()
                    except Exception:  # noqa: BLE001
                        pass
                return {"ok": True, "action": "opened from the menu", "target": target,
                        "screen_title": page.title(),
                        "url_path": re.sub(r"^https?://[^/]+", "", page.url or ""),
                        "visible_controls": visible_controls(page)}
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
            _close_blocking_dialog(page, el)
            try:
                el.click(timeout=2500)
            except Exception:  # noqa: BLE001
                # 13:24 run: 'Phonebook'/'Dashboard' clicks timed out 4s (covered by a
                # leftover modal / collapsed label). Go to the link target instead.
                href = _hidden_link_href(page, target)
                if not href:
                    raise
                page.goto(href, wait_until="domcontentloaded", timeout=8000)
                action = "opened from the menu"
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


ACTIVE_SCREENS: list = []


def stop_all_screens() -> int:
    """Demo end: stop every screen-capture thread (it outlived the 14:29 demo)."""
    n = 0
    for sc in list(ACTIVE_SCREENS):
        try:
            sc.stop()
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


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
        if prev and (not prev[1].is_set() or time.monotonic() - prev[0] < 6.0):
            # Gemini re-issued the same call while the first was still queued.
            prev[1].wait(timeout=25.0)
            return prev[2].get("result") or {"ok": False, "error": "screen busy"}
        done = threading.Event()
        box: dict = {"seq": _next_seq(name)}
        self.__dict__.setdefault("_recent", {})[key] = (time.monotonic(), done, box)
        print(f"[ui-tool] queued {name} {args}", flush=True)
        st = getattr(self, "screen", None)
        if st is not None and st.page is not None and not st.error:
            st.submit(name, dict(args or {}), box, done)
        else:
            self._q.put((name, dict(args or {}), box, done))
        if not done.wait(timeout=45.0 if name in ("do_task", "confirm_submit") else 12.0):
            return {"ok": False, "error": "that took too long on screen; say so honestly and offer to try "
                    "again or explain it instead", "timed_out": True}
        self.hold_until = time.monotonic() + 8.0
        return box.get("result") or {"ok": False}

    def jobs_pending(self) -> bool:
        if not self._q.empty():
            return True
        return any(not v[1].is_set() for v in (getattr(self, "_recent", {}) or {}).values())

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
                box["result"] = superseded(name, box) or dispatch(self.page, name, args, self.push_frame)
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
        gap = 0.04
        try:
            from navigator.meeting.screen_page import capture_pump_ms, video_mode

            if video_mode():
                # Follow this demo's relay, not a process-wide fps.
                screen = getattr(self, "screen", None)
                relay = getattr(screen, "relay", None) if screen is not None else None
                url = getattr(relay, "meeting_url", "") if relay is not None else ""
                gap = capture_pump_ms(url) / 1000.0
        except Exception:  # noqa: BLE001
            pass
        if now - getattr(self, "_last_spin", 0.0) < gap:
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


#: seconds of silence (no tool, no audio, no prospect speech) before the flow resumes
QUIET_S = 3.0


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
    # Resume only once the question is fully handled: no tool job running, no
    # answer audio playing, and QUIET_S of silence from both sides — not a fixed
    # timer (14:54 run resumed after 30.0s mid-conversation).
    quiet_since = None
    cap = max(max_hold_s, 240.0)
    while time.monotonic() - t0 < cap:
        ui.pump()
        busy = ui.hold_active(live) or ui.jobs_pending()
        now = time.monotonic()
        if busy:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = now
        elif now - quiet_since >= QUIET_S:
            break
        time.sleep(0.1)
    print(f"[flow] resumed after {time.monotonic() - t0:.1f}s Q&A "
          f"({'question handled' if quiet_since else 'safety cap'})", flush=True)


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
        ACTIVE_SCREENS.append(self)

    def start(self, timeout: float = 15.0) -> bool:
        self._thread.start()
        self.ready.wait(timeout)
        return self.page is not None

    def stop(self) -> None:
        self._stop.set()
        if self in ACTIVE_SCREENS:
            ACTIVE_SCREENS.remove(self)

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
                    if not browser.is_connected():
                        print("[screen-thread] browser gone — stopping", flush=True)
                        break
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
                            box["result"] = superseded(name, box) or dispatch(self.page, name, args, None)
                        except Exception as exc:  # noqa: BLE001
                            box["result"] = {"ok": False, "error": str(exc)[:200]}
                        print(f"[ui-tool] screen-thread ran {name} {args} in {time.monotonic() - t0:.2f}s -> "
                              f"ok={box['result'].get('ok')} {box['result'].get('action', box['result'].get('error', ''))}",
                              flush=True)
                        done.set()
                    if DEFERRED_TOURS and self.page is not None:
                        run_deferred_tours(self.page)
                    try:
                        # CDP events dispatch during this wait. The interval
                        # is half a capture frame in video mode (8ms at 60fps)
                        # so a setting of 60 is not held at 20ms.
                        pump_ms = 20
                        from navigator.meeting.screen_page import capture_pump_ms, video_mode

                        if video_mode():
                            url = getattr(self.relay, "meeting_url", "") if self.relay is not None else ""
                            pump_ms = capture_pump_ms(url)
                        (self.page or pg).wait_for_timeout(pump_ms)
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
    el = _find_main(page, target)
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
