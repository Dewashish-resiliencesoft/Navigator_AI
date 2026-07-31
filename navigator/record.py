"""Recorder: human clicks through an app once → draft site graph YAML.

Does not remove the human — guessed postconditions are suggestions.
Prefer data-testid / id over brittle CSS.
"""

from __future__ import annotations

import argparse
import re
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
        return _slug(text, tag), f"text={text[:40]}"
    return f"{tag}_el", tag


def guess_postcondition(step: RecordedStep) -> dict[str, Any]:
    if step.tool == "fill_field":
        return {
            "check": "value_equals",
            "selector": step.alias,
            "expected": step.value or "",
        }
    if step.tool == "click_element":
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


def _install_listeners(page: Page, steps: list[RecordedStep]) -> None:
    page.expose_binding(
        "navigatorRecord",
        lambda source, payload: steps.append(_step_from_payload(payload)),
    )
    page.add_init_script(
        """
        (() => {
          const send = (payload) => {
            try { window.navigatorRecord(payload); } catch (e) {}
          };
          document.addEventListener('click', (ev) => {
            const t = ev.target;
            if (!t || !t.tagName) return;
            send({
              tool: 'click_element',
              tag: t.tagName.toLowerCase(),
              id: t.id || '',
              name: t.getAttribute('name') || '',
              testid: t.getAttribute('data-testid') || '',
              text: (t.innerText || t.value || '').trim().slice(0, 60),
            });
          }, true);
          document.addEventListener('change', (ev) => {
            const t = ev.target;
            if (!t || !t.tagName) return;
            const tag = t.tagName.toLowerCase();
            if (!['input','textarea','select'].includes(tag)) return;
            send({
              tool: 'fill_field',
              tag,
              id: t.id || '',
              name: t.getAttribute('name') || '',
              testid: t.getAttribute('data-testid') || '',
              text: (t.getAttribute('placeholder') || tag),
              value: t.value || '',
            });
          }, true);
        })();
        """
    )


def _step_from_payload(payload: dict[str, Any]) -> RecordedStep:
    alias, css = prefer_selector(payload)
    tool = str(payload.get("tool") or "click_element")
    step = RecordedStep(
        tool=tool,
        alias=alias,
        selector=css,
        value=payload.get("value"),
    )
    step.postcondition = guess_postcondition(step)
    return step


def record_session(
    url: str,
    *,
    out_path: Path,
    product_name: str = "Recorded Product",
    headful: bool = True,
) -> Path:
    """Open browser, record clicks/fills until user closes window or presses Enter."""
    steps: list[RecordedStep] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headful)
        context = browser.new_context()
        page = context.new_page()
        _install_listeners(page, steps)
        page.goto(url, wait_until="domcontentloaded")
        print(
            "[record] Click through the demo in the browser.\n"
            "[record] When done, press Enter here to write the draft YAML.",
            flush=True,
        )
        try:
            input()
        except EOFError:
            pass
        base = url.rstrip("/")
        # strip path for base_url guess
        from urllib.parse import urlparse

        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        draft = draft_site_graph(
            base_url=base_url, product_name=product_name, steps=steps
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(yaml.safe_dump(draft, sort_keys=False), encoding="utf-8")
        print(f"[record] wrote {len(steps)} steps → {out_path}", flush=True)
        context.close()
        browser.close()
    return out_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Record a draft Navigator site graph")
    p.add_argument("--url", required=True, help="App URL to open")
    p.add_argument(
        "--out",
        default="navigator/config/sites/recorded_draft.yaml",
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
