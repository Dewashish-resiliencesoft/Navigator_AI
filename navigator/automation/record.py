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

def _narrate_widget_js() -> str:
    """Reload from disk so record workers pick up widget edits without restart."""
    return (
        Path(__file__).resolve().parent / "narrate_widget.js"
    ).read_text(encoding="utf-8")


_NARRATE_WIDGET_JS = _narrate_widget_js()


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
    #: Reuse earlier visitor answer (alias from a source=user fill).
    value_ref: str | None = None
    #: Optional spoken line for this step (next-prompt agent / mic merge).
    spoken: str | None = None
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
    # Record-studio / narrate overlay — never part of the Client's demo flow.
    if "nav-narrate" in combined or "navigator-chrome" in combined:
        return "navigator record studio chrome"
    if re.search(
        r"(start capturing|start hands|take over|nav.studio|record studio)",
        f"{alias} {text}".lower(),
    ):
        return "navigator record studio chrome"
    return None


def guess_postcondition(step: RecordedStep) -> dict[str, Any]:
    if step.tool == "fill_field":
        # Visitor / value_ref fills have no known value at record time.
        if step.source == "user" or step.value_ref:
            return {
                "check": "visible",
                "selector": step.alias,
                "timeout_ms": 5000,
            }
        return {
            "check": "value_equals",
            "selector": step.alias,
            "expected": step.value or "",
            "timeout_ms": 5000,
        }
    if step.tool == "scroll_page":
        return {"check": "visible", "selector": "body", "timeout_ms": 3000}
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
    agent_tasks: list[Any] | None = None,
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
            if step.value_ref:
                call["value_ref"] = step.value_ref
        elif step.tool == "click_element":
            call["selector"] = step.alias
        elif step.tool == "scroll_page":
            # value encodes "x,y" absolute scroll position from the recorder.
            raw = (step.value or "0,0").split(",")
            try:
                call["x"] = int(raw[0].strip())
            except (TypeError, ValueError, IndexError):
                call["x"] = 0
            try:
                call["y"] = int(raw[1].strip()) if len(raw) > 1 else 0
            except (TypeError, ValueError):
                call["y"] = 0
            if step.alias and step.alias not in {"window", "body"}:
                call["selector"] = step.alias
        elif step.tool == "navigate":
            call["page_id"] = step.page_id
        elif step.tool == "wait_for":
            call["selector"] = step.alias
        if step.spoken:
            call["spoken"] = step.spoken
        page["flows"]["recorded_demo"].append(call)

    from navigator.automation.prompt_command import agent_tasks_to_meta
    from navigator.automation.record_studio import demo_variables_from_steps

    meta: dict[str, Any] = {
        "draft": True,
        "note": (
            "Human must review: rename flows, fix postconditions, "
            "prefer data-testid. Do not auto-trust guessed expects."
        ),
        "demo_variables": demo_variables_from_steps(steps),
    }
    if agent_tasks:
        meta["agent_tasks"] = agent_tasks_to_meta(list(agent_tasks))

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
        "_meta": meta,
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
    if (raw && raw.closest && raw.closest('#nav-narrate,[data-navigator-chrome]')) return;
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
    if (raw && raw.closest && raw.closest('#nav-narrate,[data-navigator-chrome]')) return;
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
  document.addEventListener('focusin', (ev) => {
    const raw = (ev.composedPath && ev.composedPath()[0]) || ev.target;
    if (raw && raw.closest && raw.closest('#nav-narrate,[data-navigator-chrome]')) return;
    const info = elInfo(raw);
    if (!info) return;
    if (!['input','textarea','select'].includes(info.tag)) return;
    send({
      tool: 'focus_field',
      url: location.href,
      at_ms: atMs(),
      ...info,
      text: (raw.getAttribute && raw.getAttribute('placeholder')) || info.tag,
      value: (raw && raw.value) || '',
    });
  }, true);
  let scrollTimer = null;
  let lastScrollSent = { x: 0, y: 0 };
  const flushScroll = () => {
    scrollTimer = null;
    const x = Math.round(window.scrollX || window.pageXOffset || 0);
    const y = Math.round(window.scrollY || window.pageYOffset || 0);
    if (Math.abs(x - lastScrollSent.x) < 24 && Math.abs(y - lastScrollSent.y) < 24) return;
    lastScrollSent = { x, y };
    send({
      tool: 'scroll_page',
      url: location.href,
      at_ms: atMs(),
      scroll_x: x,
      scroll_y: y,
      tag: 'window',
      text: 'scroll',
    });
  };
  document.addEventListener('scroll', () => {
    if (scrollTimer) clearTimeout(scrollTimer);
    scrollTimer = setTimeout(flushScroll, 180);
  }, true);
})();
"""


def inject_narration_widget(page: Page) -> None:
    """Install record-studio + narrate overlay (idempotent). Always during record."""
    page.evaluate(_narrate_widget_js())


def inject_dom_listeners(page: Page) -> None:
    """Install click/fill listeners on the current document (idempotent per load)."""
    page.evaluate(_INJECT_JS)


@dataclass
class CaptureGate:
    """Shared mutable phase for a recording session.

    Listeners close over this so POST /record/capture can flip phase without
    restarting Playwright.
    """

    phase: str = "setup"  # setup | capturing | stopping | done
    setup_discarded: int = 0
    flagged: list[dict[str, Any]] = field(default_factory=list)
    login_config_fn: Any = None  # Callable[[], LoginConfig] | None
    allow_login_steps: bool = False
    #: stop_event for in-browser Stop button (set by record_session).
    stop_event: threading.Event | None = None
    #: Optional guided plan meta (legacy; hands UI removed from studio).
    guided_plan_meta: dict[str, Any] | None = None
    #: Callable[[dict], None] — publish studio status (mp ns or local).
    status_sink: Any = None
    #: Same list the recorder appends to (for studio mark-ask / vars).
    steps_ref: list[RecordedStep] | None = None
    #: Last focused/filled field summary for the studio chip.
    last_field: dict[str, Any] | None = None
    #: Studio Stop requested — dashboard must POST /record/stop to merge.
    needs_merge: bool = False
    #: Confirmed AgentTasks from Prompt Listening this session.
    agent_tasks: list[Any] = field(default_factory=list)


def _publish_studio_status(gate: CaptureGate | None, page: Page | None = None) -> dict[str, Any]:
    from navigator.automation.record_studio import demo_variables_from_steps

    phase = gate.phase if gate is not None else "setup"
    steps = getattr(gate, "steps_ref", None) if gate is not None else None
    variables = demo_variables_from_steps(steps or [])
    last_field = getattr(gate, "last_field", None) if gate is not None else None
    needs_merge = bool(getattr(gate, "needs_merge", False)) if gate is not None else False
    out = {
        "phase": phase,
        "hands": {"active": False},
        "last_field": last_field,
        "demo_variables": variables,
        "needs_merge": needs_merge,
    }
    sink = getattr(gate, "status_sink", None) if gate is not None else None
    if callable(sink):
        try:
            sink(out)
        except Exception:  # noqa: BLE001
            pass
    if page is not None:
        try:
            page.evaluate(
                """(st) => {
                  try {
                    window.__navStudioStatus = st;
                    if (typeof window.__navStudioApply === 'function') window.__navStudioApply(st);
                  } catch (e) {}
                }""",
                out,
            )
        except Exception:  # noqa: BLE001
            pass
    return out


def _field_chip(step: RecordedStep, *, step_index: int) -> dict[str, Any]:
    return {
        "step_index": step_index,
        "tool": step.tool,
        "alias": step.alias,
        "selector": step.selector,
        "source": step.source,
        "value_ref": step.value_ref,
        "live_question": step.live_question,
        "label": (step.alias or "field").replace("_", " "),
    }


def _install_listeners(
    page: Page,
    steps: list[RecordedStep],
    *,
    gate: CaptureGate | None = None,
    narration: NarrationCapture | None = None,
    bind_only: bool = False,
) -> None:
    """Expose binding + inject click/fill capture (and re-inject on every navigation).

    `add_init_script` alone is not enough: SPA navigations, CSP timing, and silent
    `catch` meant clicks often never reached Python → 0 steps. We also evaluate into
    the live document after each load.

    Phase gate lives here (Python), not in JS — the browser is untrusted.

    ``bind_only=True``: expose CDP bindings on about:blank *before* goto (remote
    CDP after nav can stall). Call again after load to inject the live overlay.
    """
    if gate is not None:
        gate.steps_ref = steps

    def _on_payload(payload: object) -> None:
        if not isinstance(payload, dict):
            return
        tool = str(payload.get("tool") or "")
        # Focus chip only — never a flow step.
        if tool == "focus_field":
            if gate is None or gate.phase != "capturing":
                return
            alias, selector = prefer_selector(payload)
            gate.last_field = {
                "step_index": None,
                "tool": "focus_field",
                "alias": alias,
                "selector": selector,
                "source": "agent",
                "value_ref": None,
                "live_question": None,
                "label": alias.replace("_", " "),
            }
            _publish_studio_status(gate, page)
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
        # Coalesce consecutive scrolls into the final resting position.
        if (
            step.tool == "scroll_page"
            and steps
            and steps[-1].tool == "scroll_page"
        ):
            steps[-1] = step
            print(
                f"[record] ~scroll_page → {step.value!r}",
                flush=True,
            )
            return
        steps.append(step)
        if gate is not None and step.tool in {"fill_field", "click_element"}:
            tag = str(payload.get("tag") or "").lower()
            if step.tool == "fill_field" or tag in {"input", "textarea", "select"}:
                gate.last_field = _field_chip(step, step_index=len(steps) - 1)
                _publish_studio_status(gate, page)
        print(
            f"[record] +{step.tool} alias={step.alias!r} sel={step.selector!r}"
            + (f" value={step.value!r}" if step.value is not None else ""),
            flush=True,
        )

    def _studio_status(_: object = None) -> dict[str, Any]:
        return _publish_studio_status(gate, page)

    def _handle_studio_action(payload: object) -> dict[str, Any]:
        """Shared handler for expose_function + DOM data-nav-studio-cmd bridge."""
        if isinstance(payload, str):
            raw = payload.strip()
            if not raw:
                return {"ok": False, "error": "empty"}
            try:
                import json as _json

                payload = _json.loads(raw)
            except Exception:  # noqa: BLE001
                payload = {"action": raw}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "bad payload"}
        action = str(payload.get("action") or "").strip()
        if gate is None:
            return {"ok": False, "error": "no gate"}
        if action == "begin_capture":
            gate.phase = "capturing"
            print("[record] studio: begin_capture", flush=True)
            return _publish_studio_status(gate, page)
        if action == "stop_record":
            print("[record] studio: stop_record → needs_merge", flush=True)
            gate.needs_merge = True
            gate.phase = "stopping"
            if gate.stop_event is not None:
                gate.stop_event.set()
            st = _publish_studio_status(gate, page)
            st["ok"] = True
            return st
        if action == "mark_field_ask":
            from navigator.automation.record_studio import mark_step_ask_visitor

            try:
                idx = payload.get("step_index")
                if idx is None and gate.last_field and gate.last_field.get("step_index") is None:
                    lf = gate.last_field
                    steps.append(
                        RecordedStep(
                            tool="fill_field",
                            alias=str(lf.get("alias") or "field"),
                            selector=str(lf.get("selector") or "input"),
                            value="",
                            page_id="main",
                        )
                    )
                    idx = len(steps) - 1
                elif idx is None and gate.last_field and gate.last_field.get("step_index") is not None:
                    idx = gate.last_field.get("step_index")
                step = mark_step_ask_visitor(
                    steps,
                    step_index=int(idx) if idx is not None else None,
                    var_alias=str(payload.get("var_alias") or ""),
                    live_question=str(
                        payload.get("live_question") or payload.get("prompt") or ""
                    ),
                    page=page,
                )
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
            for i in range(len(steps) - 1, -1, -1):
                if steps[i] is step or (
                    steps[i].alias == step.alias and steps[i].source == "user"
                ):
                    gate.last_field = _field_chip(steps[i], step_index=i)
                    break
            st = _publish_studio_status(gate, page)
            st["ok"] = True
            return st
        if action == "bind_value_ref":
            from navigator.automation.record_studio import bind_value_ref

            try:
                idx = payload.get("step_index")
                if idx is None and gate.last_field and gate.last_field.get("step_index") is None:
                    lf = gate.last_field
                    steps.append(
                        RecordedStep(
                            tool="fill_field",
                            alias=str(lf.get("alias") or "field"),
                            selector=str(lf.get("selector") or "input"),
                            value="",
                            page_id="main",
                        )
                    )
                    idx = len(steps) - 1
                elif idx is None and gate.last_field and gate.last_field.get("step_index") is not None:
                    idx = gate.last_field.get("step_index")
                step = bind_value_ref(
                    steps,
                    step_index=int(idx) if idx is not None else None,
                    value_ref=str(payload.get("value_ref") or ""),
                )
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
            for i in range(len(steps) - 1, -1, -1):
                if steps[i] is step or steps[i].value_ref == step.value_ref:
                    gate.last_field = _field_chip(steps[i], step_index=i)
                    break
            st = _publish_studio_status(gate, page)
            st["ok"] = True
            return st
        if action == "keep_agent_fill":
            gate.last_field = None
            st = _publish_studio_status(gate, page)
            st["ok"] = True
            return st
        if action == "dismiss_field":
            gate.last_field = None
            st = _publish_studio_status(gate, page)
            st["ok"] = True
            return st
        if action == "next_prompt":
            from navigator.automation.record_studio import (
                demo_variables_from_steps,
                propose_next_steps,
            )

            try:
                extra = propose_next_steps(
                    page=page,
                    client_prompt=str(payload.get("prompt") or ""),
                    variables=demo_variables_from_steps(steps),
                    graph_snippet=str(payload.get("graph_snippet") or ""),
                )
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
            steps.extend(extra)
            st = _publish_studio_status(gate, page)
            st["ok"] = True
            st["added"] = len(extra)
            return st
        if action == "prompt_parse":
            from navigator.automation.prompt_command import parse_agent_task_instruction

            try:
                task = parse_agent_task_instruction(
                    str(payload.get("instruction") or ""),
                    current_field=gate.last_field,
                    use_llm=bool(payload.get("use_llm", True)),
                )
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
            st = _publish_studio_status(gate, page)
            st["ok"] = True
            st["agent_task"] = task.to_dict()
            return st
        if action == "prompt_confirm":
            from navigator.automation.prompt_command import AgentTask
            from navigator.automation.record_studio import apply_confirmed_agent_task

            try:
                raw_task = payload.get("agent_task") or {}
                if not isinstance(raw_task, dict):
                    raise RuntimeError("agent_task required")
                task = AgentTask.from_dict(raw_task)
                task.status = "confirmed"
                apply_confirmed_agent_task(steps, task, page=page)
                gate.agent_tasks = list(getattr(gate, "agent_tasks", None) or [])
                gate.agent_tasks.append(task)
                if narration is not None:
                    narration.agent_tasks = list(gate.agent_tasks)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
            st = _publish_studio_status(gate, page)
            st["ok"] = True
            st["agent_task"] = task.to_dict()
            st["demo_variables"] = st.get("demo_variables") or []
            return st
        return {"ok": False, "error": f"unknown action {action}"}

    def _studio_cmd(payload: object) -> dict[str, Any]:
        return _handle_studio_action(payload)

    def _drain_dom_studio_cmd() -> None:
        """Fallback when expose_function fails on remote CDP after navigation."""
        try:
            raw = page.evaluate(
                """() => {
                  const el = document.documentElement;
                  const v = el.getAttribute('data-nav-studio-cmd');
                  if (v) el.removeAttribute('data-nav-studio-cmd');
                  return v || '';
                }"""
            )
        except Exception:  # noqa: BLE001
            return
        if not raw:
            return
        try:
            st = _handle_studio_action(raw)
            if isinstance(st, dict) and st.get("phase"):
                _publish_studio_status(gate, page)
        except Exception as exc:  # noqa: BLE001
            print(f"[record] dom studio cmd failed: {exc}", flush=True)

    # Wait loop + reinject call this without closing over install scope.
    if gate is not None:
        gate.drain_dom_studio_cmd = _drain_dom_studio_cmd  # type: ignore[attr-defined]

    already = bool(getattr(page, "_nav_record_bound", False))
    if not already:
        # Bind before goto when possible — remote CDP expose after nav can stall.
        print("[record] binding studio functions…", flush=True)
        page.expose_function("navigatorRecord", _on_payload)
        page.expose_function("navigatorStudioCmd", _studio_cmd)
        page.expose_function("navigatorStudioStatus", _studio_status)
        page.add_init_script(_INJECT_JS)
        page.add_init_script(_narrate_widget_js())
        if narration is not None:
            page.expose_function("navigatorNarrate", narration.on_chunk)
            page.expose_function("navigatorNarrateConfig", narration.apply_config)
        page._nav_record_bound = True  # type: ignore[attr-defined]
        print("[record] studio functions bound", flush=True)

    if bind_only:
        return

    def _reinject() -> None:
        try:
            from navigator.automation.browser.cursor import install_cursor

            install_cursor(page)
            inject_dom_listeners(page)
            inject_narration_widget(page)
        except Exception as exc:  # noqa: BLE001
            print(f"[record] reinject skipped: {exc}", flush=True)

    if not getattr(page, "_nav_record_load_hook", False):
        page.on("load", lambda *_: _reinject())
        page.on("framenavigated", lambda frame: _reinject() if frame == page.main_frame else None)
        page._nav_record_load_hook = True  # type: ignore[attr-defined]
    _reinject()


def _step_from_payload(payload: dict[str, Any]) -> RecordedStep:
    from navigator.automation.login_match import (
        VAULT_PASSWORD_SENTINEL,
        is_password_field,
    )

    tool = str(payload.get("tool") or "click_element")
    if tool == "scroll_page":
        try:
            sx = int(payload.get("scroll_x") or payload.get("x") or 0)
            sy = int(payload.get("scroll_y") or payload.get("y") or 0)
        except (TypeError, ValueError):
            sx, sy = 0, 0
        try:
            at_ms = int(payload.get("at_ms") or 0)
        except (TypeError, ValueError):
            at_ms = 0
        step = RecordedStep(
            tool="scroll_page",
            alias="window",
            selector="body",
            value=f"{sx},{sy}",
            at_ms=at_ms,
        )
        step.postcondition = guess_postcondition(step)
        return step

    alias, css = prefer_selector(payload)
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
    #: Confirmed AgentTasks from Prompt Listening (persisted into graph _meta).
    agent_tasks: list[Any] = field(default_factory=list)

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
    from navigator.automation.playwright_env import (
        ensure_headed_display,
        ensure_playwright_browsers,
    )

    ensure_playwright_browsers()
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
            if headful:
                ensure_headed_display()
            try:
                browser = pw.chromium.launch(
                    headless=not headful,
                    args=["--start-maximized"] if headful else [],
                )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if "Missing X server" in msg or "DISPLAY" in msg or "Target page, context or browser has been closed" in msg:
                    raise RuntimeError(
                        "Headed record failed (no display). On VPS set DISPLAY=:0 "
                        "with Xvfb, or run scripts/local_record_server.py on your "
                        "laptop and set NAVIGATOR_RECORD_BROWSER_WS."
                    ) from exc
                raise

        # Headful record: use the real window size (maximized). A fixed
        # 1280×720 viewport inside a maximized Chrome looks like a white
        # letterbox with the site stuck in one corner.
        if headful:
            context = browser.new_context(no_viewport=True)
        else:
            context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        if stop_event is not None:
            gate.stop_event = stop_event

        def _plan_sink(meta: dict[str, Any]) -> None:
            gate.guided_plan_meta = meta
            try:
                gate._plan_dirty = True  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            sink = getattr(gate, "status_sink", None)
            if callable(sink):
                try:
                    sink(
                        {
                            "phase": gate.phase,
                            "hands": {"active": False},
                            "plan_meta": meta,
                            "plan_dirty": True,
                            "needs_merge": bool(getattr(gate, "needs_merge", False)),
                        }
                    )
                except Exception:  # noqa: BLE001
                    pass

        page._nav_plan_sink = _plan_sink  # type: ignore[attr-defined]
        target = (url or "").strip()
        if target and "://" not in target:
            target = f"https://{target}"
        # Bind CDP functions on about:blank first — after remote goto, expose can hang.
        try:
            _install_listeners(
                page, steps, gate=gate, narration=narration, bind_only=True
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[record] bind-before-goto failed: {exc}", flush=True)
        print(f"[record] opening {target!r}", flush=True)
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=60_000)
            print(f"[record] loaded {page.url!r}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[record] goto failed: {exc}", flush=True)
            try:
                page.set_content(
                    "<html><body style='font:16px system-ui;padding:2rem'>"
                    "<h1>Could not open start URL</h1>"
                    f"<p><code>{target}</code></p>"
                    f"<pre>{exc}</pre>"
                    "<p>Check the Start URL in the dashboard and try again.</p>"
                    "</body></html>"
                )
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                page.bring_to_front()
            except Exception:  # noqa: BLE001
                pass
        if narration is not None and target and "://" in target:
            from urllib.parse import urlparse as _urlparse

            parsed_origin = _urlparse(target)
            try:
                context.grant_permissions(
                    ["microphone"],
                    origin=f"{parsed_origin.scheme}://{parsed_origin.netloc}",
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[record] mic permission not granted: {exc}", flush=True)
        try:
            _install_listeners(page, steps, gate=gate, narration=narration)
            print("[record] studio listeners ready", flush=True)
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
                "[record] Ops session: use Record studio overlay in the browser "
                "(Start capturing this flow / Stop), or /client dashboard.",
                flush=True,
            )
            last_phase = gate.phase if gate is not None else "setup"
            while not stop_event.wait(0.25):
                drain = getattr(gate, "drain_dom_studio_cmd", None) if gate else None
                if callable(drain):
                    try:
                        drain()
                    except Exception:  # noqa: BLE001
                        pass
                if gate is not None and gate.phase != last_phase:
                    last_phase = gate.phase
                    print(f"[record] phase → {last_phase}", flush=True)
                if gate is not None and getattr(gate, "needs_merge", False):
                    if gate.stop_event is not None and not gate.stop_event.is_set():
                        gate.stop_event.set()
                    break
                _publish_studio_status(gate, page)
        from urllib.parse import urlparse

        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        draft = draft_site_graph(
            base_url=base_url,
            product_name=product_name,
            steps=steps,
            agent_tasks=list(getattr(gate, "agent_tasks", None) or []),
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
            # Don't hang dashboard Stop on a wedged remote Chrome context.
            def _close_browser() -> None:
                try:
                    context.close()
                except Exception:  # noqa: BLE001
                    pass
                if not ws:
                    try:
                        browser.close()
                    except Exception:  # noqa: BLE001
                        pass

            closer = threading.Thread(target=_close_browser, name="record-close", daemon=True)
            closer.start()
            closer.join(timeout=4.0)
            if closer.is_alive():
                print("[record] browser close timed out — continuing", flush=True)
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
