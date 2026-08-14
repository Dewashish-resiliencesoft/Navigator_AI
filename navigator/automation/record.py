"""Recorder: human clicks through an app once → draft site graph YAML.

Does not remove the human — guessed postconditions are suggestions.
Prefer data-testid / id over brittle CSS.
"""

from __future__ import annotations

import argparse
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from playwright.sync_api import Page, sync_playwright

_NARRATE_WIDGET_JS = (
    Path(__file__).resolve().parent / "narrate_widget.js"
).read_text(encoding="utf-8")


@dataclass
class RecordedStep:
    tool: str
    alias: str
    selector: str
    value: str | None = None
    page_id: str = "main"
    postcondition: dict[str, Any] = field(default_factory=dict)
    #: "user" → live demo pauses for End User input (requires_live_input).
    source: str = "agent"
    live_question: str | None = None
    #: Recorded but never executed: a mutating step (submit / send / pay) the
    #: guardrail refused. Stays out of a live demo until a human approves it.
    needs_approval: bool = False
    approval_reason: str = ""
    #: Milliseconds into a narrated recording when this step happened.
    at_ms: int = 0
    #: Mouse positions leading to this step (viewport coords, ms from narrate t0).
    mouse_path: list[dict[str, int]] = field(default_factory=list)


def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return (s[:40] or fallback)


def prefer_selector(el_info: dict[str, Any]) -> tuple[str, str]:
    """Return (alias, css) preferring testid/id."""
    testid = el_info.get("testid") or ""
    eid = el_info.get("id") or ""
    name = el_info.get("name") or ""
    tag = el_info.get("tag") or "el"
    text = el_info.get("text") or ""
    if testid:
        return _slug(testid, "el"), f'[data-testid="{testid}"]'
    if eid:
        return _slug(eid, "el"), f"#{eid}"
    if name:
        return _slug(name, "el"), f'{tag}[name="{name}"]'
    if text:
        # One short line only — multi-line dumps become unusable text= selectors.
        first = text.splitlines()[0].strip()[:40]
        return _slug(first, tag), f"text={first}"
    return f"{tag}_el", tag


def junk_record_reason(el_info: dict[str, Any], *, alias: str, selector: str) -> str | None:
    """Why this click should not enter the saved flow (recorder noise)."""
    tag = (el_info.get("tag") or "").lower()
    text = (el_info.get("text") or "").strip()
    role = (el_info.get("role") or "").lower()
    if tag in {"svg", "path", "circle", "rect", "g", "line", "polyline", "polygon"}:
        return "decorative svg"
    if selector in {"svg", "path", "div", "span", "button", "a", "body", "html", "img"}:
        return f"bare tag selector ({selector})"
    if selector.lower().startswith("text="):
        label = selector.split("=", 1)[-1].strip().strip("'\"")
        # Accidental particle clicks from noisy recordings (not CTA labels).
        if label in {"on", "in", "or", "to", "of", "at", "by", "as", "is", "the"}:
            return f"too-short text selector ({selector})"
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "label", "li"}:
        return f"non-interactive text ({tag})"
    if role == "heading":
        return "non-interactive heading"
    if "\n" in text or text.count(" ") > 10:
        return "multi-line chrome dump"
    if re.search(r"verify your account", text, re.I):
        return "verify / banner chrome"
    if alias.startswith(("svg_", "div_", "span_", "path_")) and not el_info.get("testid"):
        return "generic element alias"
    combined = f"{alias} {selector}".lower()
    if re.search(
        r"(dark[_-]?mode|light[_-]?mode|theme[_-]?toggle|color[_-]?scheme)",
        combined,
    ):
        return "theme toggle chrome"
    return None


def guess_postcondition(step: RecordedStep) -> dict[str, Any]:
    if step.tool == "fill_field":
        return {
            "check": "value_equals",
            "selector": step.alias,
            "expected": step.value or "",
            "timeout_ms": 5000,
        }
    if step.tool == "click_element":
        alias = (step.alias or "").lower()
        if any(w in alias for w in ("close", "dismiss", "accept", "got_it", "ok")):
            return {"check": "hidden", "selector": step.alias, "timeout_ms": 5000}
        # Never re-assert the clicked control still visible — CTAs navigate away.
        return {"check": "visible", "selector": "body", "timeout_ms": 3000}
    if step.tool == "navigate":
        return {"check": "url_matches", "expected": step.value or "/"}
    return {"check": "visible", "selector": step.alias, "timeout_ms": 5000}


def draft_site_graph(
    *,
    base_url: str,
    product_name: str,
    steps: list[RecordedStep],
) -> dict[str, Any]:
    pages: dict[str, Any] = {}
    for step in steps:
        page = pages.setdefault(
            step.page_id,
            {
                "name": step.page_id.replace("_", " ").title(),
                "path": "/",
                "elements": {},
                "flows": {"recorded_demo": []},
            },
        )
        page["elements"].setdefault("body", "body")
        if step.alias and step.selector:
            page["elements"][step.alias] = step.selector
        pc = step.postcondition or guess_postcondition(step)
        call: dict[str, Any] = {"tool": step.tool, "expects": pc}
        if step.tool == "fill_field":
            call["selector"] = step.alias
            call["value"] = step.value or ""
            if step.source == "user":
                call["source"] = "user"
                if step.live_question:
                    call["live_question"] = step.live_question
        elif step.tool == "click_element":
            call["selector"] = step.alias
        elif step.tool == "navigate":
            call["page_id"] = step.page_id
        elif step.tool == "wait_for":
            call["selector"] = step.alias
        page["flows"]["recorded_demo"].append(call)

    return {
        "product": {
            "name": product_name,
            "base_url": base_url,
            "persona": {
                "product_name": product_name,
                "one_liner": "Recorded draft — review aliases and postconditions.",
                "agent_name": "Navigator",
            },
        },
        "pages": pages,
        "_meta": {
            "draft": True,
            "note": (
                "Human must review: rename flows, fix postconditions, "
                "prefer data-testid. Do not auto-trust guessed expects."
            ),
        },
    }


_INJECT_JS = """
(() => {
  if (document.documentElement.dataset.navigatorRecord === '1') return;
  document.documentElement.dataset.navigatorRecord = '1';
  const send = (payload) => {
    try {
      const r = window.navigatorRecord(payload);
      if (r && typeof r.catch === 'function') r.catch(() => {});
    } catch (e) {
      console.warn('[navigator-record] send failed', e);
    }
  };
  const elInfo = (t) => {
    if (!t || !t.tagName) return null;
    // Prefer the interactive ancestor — raw SVG/path clicks become useless selectors.
    let node = t;
    for (let i = 0; i < 6 && node; i++) {
      const tag = (node.tagName || '').toLowerCase();
      const role = (node.getAttribute && node.getAttribute('role')) || '';
      const tid = (node.getAttribute && node.getAttribute('data-testid')) || '';
      const id = node.id || '';
      if (tid || id || tag === 'button' || tag === 'a' || tag === 'input' ||
          tag === 'textarea' || tag === 'select' ||
          role === 'button' || role === 'link' || role === 'menuitem') {
        t = node;
        break;
      }
      node = node.parentElement;
    }
    const rawText = (t.innerText || t.value || '').trim();
    const firstLine = rawText.split('\\n').map(s => s.trim()).filter(Boolean)[0] || '';
    return {
      tag: t.tagName.toLowerCase(),
      id: t.id || '',
      name: t.getAttribute('name') || '',
      testid: t.getAttribute('data-testid') || '',
      text: firstLine.slice(0, 60),
      type: (t.getAttribute && t.getAttribute('type')) || t.type || '',
      autocomplete: (t.getAttribute && t.getAttribute('autocomplete')) || '',
    };
  };
  // Elapsed since narration started, so audio and steps share one clock.
  const atMs = () => {
    const t0 = window.__navNarrateT0;
    return t0 ? Math.round(performance.now() - t0) : 0;
  };
  let trace = [];
  let lastMoveAt = 0;
  let lastX = 0;
  let lastY = 0;
  const MIN_MOVE_MS = 16;
  const MIN_MOVE_PX = 2;
  document.addEventListener('mousemove', (ev) => {
    const now = performance.now();
    const x = ev.clientX;
    const y = ev.clientY;
    const c = document.getElementById('nav-cursor');
    if (c) {
      c.style.left = x + 'px';
      c.style.top = y + 'px';
    }
    if (now - lastMoveAt < MIN_MOVE_MS) return;
    const dx = x - lastX;
    const dy = y - lastY;
    if (trace.length && Math.hypot(dx, dy) < MIN_MOVE_PX) return;
    lastMoveAt = now;
    lastX = x;
    lastY = y;
    trace.push({ x: Math.round(x), y: Math.round(y), at_ms: atMs() });
    if (trace.length > 800) trace.shift();
  }, true);
  document.addEventListener('click', (ev) => {
    const raw = (ev.composedPath && ev.composedPath()[0]) || ev.target;
    if (raw && raw.closest && raw.closest('#nav-narrate')) return;
    const info = elInfo(raw);
    if (!info) return;
    const clickPt = {
      x: Math.round(ev.clientX),
      y: Math.round(ev.clientY),
      at_ms: atMs(),
    };
    const path = trace.slice();
    path.push(clickPt);
    send({
      tool: 'click_element',
      url: location.href,
      at_ms: clickPt.at_ms,
      mouse_path: path,
      ...info,
    });
    trace = [];
  }, true);
  document.addEventListener('change', (ev) => {
    const raw = (ev.composedPath && ev.composedPath()[0]) || ev.target;
    const info = elInfo(raw);
    if (!info) return;
    const tag = info.tag;
    if (!['input','textarea','select'].includes(tag)) return;
    const pt = {
      x: Math.round((ev.clientX != null ? ev.clientX : lastX) || 0),
      y: Math.round((ev.clientY != null ? ev.clientY : lastY) || 0),
      at_ms: atMs(),
    };
    const path = trace.slice();
    if (pt.x || pt.y) path.push(pt);
    send({
      tool: 'fill_field',
      url: location.href,
      at_ms: pt.at_ms,
      mouse_path: path,
      ...info,
      text: (raw.getAttribute && raw.getAttribute('placeholder')) || tag,
      value: raw.value || '',
    });
    trace = [];
  }, true);
})();
"""


def inject_narration_widget(page: Page) -> None:
    """Install the narrate overlay on the current document (idempotent)."""
    page.evaluate(_NARRATE_WIDGET_JS)


def inject_dom_listeners(page: Page) -> None:
    """Install click/fill listeners on the current document (idempotent per load)."""
    page.evaluate(_INJECT_JS)


def _install_listeners(
    page: Page,
    steps: list[RecordedStep],
    *,
    gate: CaptureGate | None = None,
    narration: NarrationCapture | None = None,
) -> None:
    """Expose binding + inject click/fill capture (and re-inject on every navigation).

    `add_init_script` alone is not enough: SPA navigations, CSP timing, and silent
    `catch` meant clicks often never reached Python → 0 steps. We also evaluate into
    the live document after each load.

    Phase gate lives here (Python), not in JS — the browser is untrusted.
    """

    def _on_payload(payload: object) -> None:
        if not isinstance(payload, dict):
            return
        step = _step_from_payload(payload)
        if gate is not None and gate.phase != "capturing":
            gate.setup_discarded += 1
            print(
                f"[record] setup discard +{step.tool} "
                f"(phase={gate.phase}, discarded={gate.setup_discarded})",
                flush=True,
            )
            return
        junk = junk_record_reason(
            payload, alias=step.alias, selector=step.selector
        )
        if junk and step.tool == "click_element":
            print(f"[record] skip junk click ({junk}): {step.alias!r}", flush=True)
            return
        # Defense-in-depth: login-shaped steps stay out of ordinary flows. Auth
        # walkthrough recordings (authentication_flow, etc.) keep sign-in clicks.
        if (
            gate is not None
            and gate.login_config_fn is not None
            and not gate.allow_login_steps
        ):
            from navigator.automation.login_match import looks_like_login

            reason = looks_like_login(
                config=gate.login_config_fn(),
                element={
                    "type": payload.get("type") or "",
                    "autocomplete": payload.get("autocomplete") or "",
                },
                url=str(payload.get("url") or page.url or ""),
                selector=step.selector or step.alias or "",
            )
            if reason:
                gate.flagged.append(
                    {
                        "tool": step.tool,
                        "selector": step.selector,
                        "alias": step.alias,
                        "reason": reason,
                    }
                )
                print(f"[record] flagged login step: {reason}", flush=True)
                return
        steps.append(step)
        print(
            f"[record] +{step.tool} alias={step.alias!r} sel={step.selector!r}"
            + (f" value={step.value!r}" if step.value is not None else ""),
            flush=True,
        )

    # expose_function: JS window.navigatorRecord(payload) → Python (no source arg).
    page.expose_function("navigatorRecord", _on_payload)
    page.add_init_script(_INJECT_JS)

    if narration is not None:
        page.expose_function("navigatorNarrate", narration.on_chunk)
        page.expose_function("navigatorNarrateConfig", narration.apply_config)
        page.add_init_script(_NARRATE_WIDGET_JS)

    def _reinject() -> None:
        try:
            inject_dom_listeners(page)
            if narration is not None:
                inject_narration_widget(page)
        except Exception as exc:  # noqa: BLE001
            print(f"[record] reinject skipped: {exc}", flush=True)

    page.on("load", lambda *_: _reinject())
    _reinject()


def _step_from_payload(payload: dict[str, Any]) -> RecordedStep:
    from navigator.automation.login_match import (
        VAULT_PASSWORD_SENTINEL,
        is_password_field,
    )

    alias, css = prefer_selector(payload)
    tool = str(payload.get("tool") or "click_element")
    value = payload.get("value")
    # Never persist a typed secret. Sentinel tells EXECUTING to pull from vault.
    if is_password_field(
        {
            "type": payload.get("type") or "",
            "autocomplete": payload.get("autocomplete") or "",
        }
    ):
        value = VAULT_PASSWORD_SENTINEL if tool == "fill_field" else value
    try:
        at_ms = int(payload.get("at_ms") or 0)
    except (TypeError, ValueError):
        at_ms = 0
    mouse_path: list[dict[str, int]] = []
    raw_path = payload.get("mouse_path")
    if isinstance(raw_path, list):
        for pt in raw_path:
            if not isinstance(pt, dict):
                continue
            try:
                mouse_path.append(
                    {
                        "x": int(pt.get("x") or 0),
                        "y": int(pt.get("y") or 0),
                        "at_ms": int(pt.get("at_ms") or 0),
                    }
                )
            except (TypeError, ValueError):
                continue
    step = RecordedStep(
        tool=tool,
        alias=alias,
        selector=css,
        value=value,
        at_ms=at_ms,
        mouse_path=mouse_path,
    )
    step.postcondition = guess_postcondition(step)
    return step


@dataclass
class NarrationCapture:
    """Audio chunks the in-page widget streams back while the human narrates.

    MediaRecorder emits a self-contained container in the FIRST chunk only; the
    rest are continuation fragments of the same stream. So they are concatenated
    in arrival order and decoded as one clip, never individually.
    """

    mime: str = ""
    chunks: list[bytes] = field(default_factory=list)
    language: str = "auto"
    translate_to: str = "same"

    def apply_config(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        lang = str(payload.get("language") or "").strip()
        if lang:
            self.language = lang
        if "translate_to" in payload:
            self.translate_to = str(payload.get("translate_to") or "same").strip() or "same"

    def on_chunk(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        self.apply_config(payload)
        import base64

        b64 = str(payload.get("b64") or "")
        if not b64:
            return
        if not self.mime:
            self.mime = str(payload.get("mime") or "")
        try:
            self.chunks.append(base64.b64decode(b64))
        except Exception as exc:  # noqa: BLE001
            print(f"[record] narration chunk dropped: {exc}", flush=True)

    def audio(self) -> bytes:
        return b"".join(self.chunks)


@dataclass
class CaptureGate:
    """Shared mutable phase for a recording session.

    Listeners close over this so POST /record/capture can flip phase without
    restarting Playwright.
    """

    phase: str = "setup"  # setup | capturing | done
    setup_discarded: int = 0
    flagged: list[dict[str, Any]] = field(default_factory=list)
    login_config_fn: Any = None  # Callable[[], LoginConfig] | None
    allow_login_steps: bool = False


def record_session(
    url: str,
    *,
    out_path: Path,
    product_name: str = "Recorded Product",
    headful: bool = True,
    stop_event: threading.Event | None = None,
    steps_out: list[RecordedStep] | None = None,
    gate: CaptureGate | None = None,
    narration: NarrationCapture | None = None,
    browser_ws: str = "",
) -> Path:
    """Open browser, record clicks/fills until Enter — or until `stop_event` is set."""
    steps: list[RecordedStep] = steps_out if steps_out is not None else []
    # CLI path has no Setup/Recording UI — capture immediately.
    if gate is None:
        gate = CaptureGate(phase="capturing")
    with sync_playwright() as pw:
        ws = (browser_ws or "").strip()
        if ws:
            print("[record] connecting to local Playwright server", flush=True)
            try:
                browser = pw.chromium.connect(ws, timeout=20_000)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    "local record browser not reachable — on your laptop run "
                    ".venv/bin/python scripts/local_record_server.py"
                ) from exc
        else:
            browser = pw.chromium.launch(headless=not headful)
        # Match live-demo screenshare viewport so recorded mouse coords replay 1:1.
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        if narration is not None:
            # Real mic, granted per-origin. Never --use-fake-ui-for-media-stream:
            # that feeds silence and the Client's walkthrough is lost.
            from urllib.parse import urlparse as _urlparse

            parsed_origin = _urlparse(url)
            try:
                context.grant_permissions(
                    ["microphone"],
                    origin=f"{parsed_origin.scheme}://{parsed_origin.netloc}",
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[record] mic permission not granted: {exc}", flush=True)
        page = context.new_page()
        _install_listeners(page, steps, gate=gate, narration=narration)
        page.goto(url, wait_until="domcontentloaded")
        try:
            from navigator.automation.browser.cursor import install_cursor

            install_cursor(page)
            inject_dom_listeners(page)
            if narration is not None:
                inject_narration_widget(page)
        except Exception as exc:  # noqa: BLE001
            print(f"[record] post-goto inject skipped: {exc}", flush=True)
        if stop_event is None:
            print(
                "[record] Click through the demo in the browser "
                "(each click/fill should print [record] +… here).\n"
                "[record] Keep this window open. When done, press Enter here "
                "(do not close the browser first).",
                flush=True,
            )
            try:
                input()
            except EOFError:
                pass
        else:
            print(
                "[record] Ops session: log in during Setup, then "
                "'Start capturing this flow' in /client.",
                flush=True,
            )
            while not stop_event.wait(0.25):
                pass
        from urllib.parse import urlparse

        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        draft = draft_site_graph(
            base_url=base_url, product_name=product_name, steps=steps
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(yaml.safe_dump(draft, sort_keys=False), encoding="utf-8")
        print(f"[record] wrote {len(steps)} steps → {out_path}", flush=True)
        if not steps:
            print(
                "[record] WARNING: 0 steps captured. Retry: keep Playwright Chrome "
                "open, click controls (not only empty page chrome), watch for "
                "[record] +click_element lines, then press Enter.",
                flush=True,
            )
        try:
            context.close()
            # Connected local server must stay up for the next Record click.
            if not ws:
                browser.close()
        except Exception as exc:  # noqa: BLE001
            # User often closes the window before Enter — ignore TargetClosedError noise.
            print(f"[record] browser already closed ({exc.__class__.__name__})", flush=True)
    return out_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Record a draft Navigator site graph")
    p.add_argument("--url", required=True, help="App URL to open")
    p.add_argument(
        "--out",
        default="navigator/knowledge/sites/recorded_draft.yaml",
        help="Output YAML path",
    )
    p.add_argument("--product-name", default="Recorded Product")
    p.add_argument("--headless", action="store_true")
    args = p.parse_args(argv)
    record_session(
        args.url,
        out_path=Path(args.out),
        product_name=args.product_name,
        headful=not args.headless,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
