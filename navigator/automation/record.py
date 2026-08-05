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


@dataclass
class RecordedStep:
    tool: str
    alias: str
    selector: str
    value: str | None = None
    page_id: str = "main"
    postcondition: dict[str, Any] = field(default_factory=dict)


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
    if tag in {"svg", "path", "circle", "rect", "g", "line", "polyline", "polygon"}:
        return "decorative svg"
    if selector in {"svg", "path", "div", "span", "button", "a", "body", "html"}:
        return f"bare tag selector ({selector})"
    if "\n" in text or text.count(" ") > 10:
        return "multi-line chrome dump"
    if re.search(r"verify your account", text, re.I):
        return "verify / banner chrome"
    if alias.startswith(("svg_", "div_", "span_", "path_")) and not el_info.get("testid"):
        return "generic element alias"
    return None


def guess_postcondition(step: RecordedStep) -> dict[str, Any]:
    if step.tool == "fill_field":
        return {
            "check": "value_equals",
            "selector": step.alias,
            "expected": step.value or "",
        }
    if step.tool == "click_element":
        alias = (step.alias or "").lower()
        if any(w in alias for w in ("close", "dismiss", "accept", "got_it", "ok")):
            return {"check": "hidden", "selector": step.alias}
        return {"check": "visible", "selector": step.alias}
    if step.tool == "navigate":
        return {"check": "url_matches", "expected": step.value or "/"}
    return {"check": "visible", "selector": step.alias}


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
        if step.alias and step.selector:
            page["elements"][step.alias] = step.selector
        pc = step.postcondition or guess_postcondition(step)
        call: dict[str, Any] = {"tool": step.tool, "expects": pc}
        if step.tool == "fill_field":
            call["selector"] = step.alias
            call["value"] = step.value or ""
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
  document.addEventListener('click', (ev) => {
    const raw = (ev.composedPath && ev.composedPath()[0]) || ev.target;
    const info = elInfo(raw);
    if (!info) return;
    send({ tool: 'click_element', url: location.href, ...info });
  }, true);
  document.addEventListener('change', (ev) => {
    const raw = (ev.composedPath && ev.composedPath()[0]) || ev.target;
    const info = elInfo(raw);
    if (!info) return;
    const tag = info.tag;
    if (!['input','textarea','select'].includes(tag)) return;
    send({
      tool: 'fill_field',
      url: location.href,
      ...info,
      text: (raw.getAttribute && raw.getAttribute('placeholder')) || tag,
      value: raw.value || '',
    });
  }, true);
})();
"""


def inject_dom_listeners(page: Page) -> None:
    """Install click/fill listeners on the current document (idempotent per load)."""
    page.evaluate(_INJECT_JS)


def _install_listeners(
    page: Page,
    steps: list[RecordedStep],
    *,
    gate: CaptureGate | None = None,
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
        # Defense-in-depth: even while capturing, login-shaped steps are flagged
        # and kept out of the saved flow. Config is fetched live each time.
        if gate is not None and gate.login_config_fn is not None:
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

    def _reinject() -> None:
        try:
            inject_dom_listeners(page)
        except Exception as exc:  # noqa: BLE001
            print(f"[record] reinject skipped: {exc}", flush=True)

    page.on("load", lambda: _reinject())
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
    step = RecordedStep(
        tool=tool,
        alias=alias,
        selector=css,
        value=value,
    )
    step.postcondition = guess_postcondition(step)
    return step


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


def record_session(
    url: str,
    *,
    out_path: Path,
    product_name: str = "Recorded Product",
    headful: bool = True,
    stop_event: threading.Event | None = None,
    steps_out: list[RecordedStep] | None = None,
    gate: CaptureGate | None = None,
) -> Path:
    """Open browser, record clicks/fills until Enter — or until `stop_event` is set."""
    steps: list[RecordedStep] = steps_out if steps_out is not None else []
    # CLI path has no Setup/Recording UI — capture immediately.
    if gate is None:
        gate = CaptureGate(phase="capturing")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headful)
        context = browser.new_context()
        page = context.new_page()
        _install_listeners(page, steps, gate=gate)
        page.goto(url, wait_until="domcontentloaded")
        try:
            inject_dom_listeners(page)
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
