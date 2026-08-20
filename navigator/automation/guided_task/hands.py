"""Guided Agent hands — act on the recorder's Playwright page."""

from __future__ import annotations

import re
from typing import Any

from navigator.automation.explore.perceive import inventory, is_fillable
from navigator.automation.guided_task.models import GuidedStep
from navigator.automation.record import junk_record_reason, prefer_selector
from navigator.automation.browser.cursor import click_with_cursor


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _score_element(el: dict[str, Any], hint: str) -> float:
    """Higher = better match for an ACTION hint."""
    if not hint.strip():
        return 0.0
    h = _norm(hint)
    bits = [
        el.get("label") or "",
        el.get("text") or "",
        el.get("aria_label") or "",
        el.get("title") or "",
        el.get("name") or "",
        el.get("testid") or "",
        el.get("id") or "",
    ]
    combined = _norm(" ".join(str(b) for b in bits))
    if not combined:
        return 0.0
    score = 0.0
    for token in re.findall(r"[a-z0-9]{3,}", h):
        if token in combined:
            score += 1.0
    if h in combined or combined in h:
        score += 2.0
    return score


def find_action_target(
    elements: list[dict[str, Any]], hint: str, alias: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Pick best click target. Returns (best, ambiguous_list)."""
    clickable = []
    for el in elements:
        if is_fillable(el):
            continue
        a, css = prefer_selector(el)
        if junk_record_reason(el, alias=a, selector=css):
            continue
        clickable.append(el)

    scored: list[tuple[float, dict[str, Any]]] = []
    needle = hint or alias.replace("_", " ")
    for el in clickable:
        sc = _score_element(el, needle)
        if sc > 0:
            scored.append((sc, el))

    if not scored:
        return None, []

    scored.sort(key=lambda x: -x[0])
    top_score = scored[0][0]
    top = [el for sc, el in scored if sc >= top_score - 0.01]
    if len(top) > 1:
        return None, top
    return top[0], []


def find_fill_targets(
    elements: list[dict[str, Any]], hint: str, alias: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    fills = [el for el in elements if is_fillable(el)]
    scored: list[tuple[float, dict[str, Any]]] = []
    needle = hint or alias.replace("_", " ")
    for el in fills:
        sc = _score_element(el, needle)
        if sc > 0:
            scored.append((sc, el))
    if not scored:
        if len(fills) == 1:
            return fills[0], []
        return None, fills[:8]

    scored.sort(key=lambda x: -x[0])
    top_score = scored[0][0]
    top = [el for sc, el in scored if sc >= top_score - 0.01]
    if len(top) > 1:
        return None, top
    return top[0], []


def execute_guided_step(
    page,
    step: GuidedStep,
    *,
    demo_value: str = "",
) -> dict[str, Any]:
    """Run one guided step on the open recorder page. USER_INPUT never fills real data."""
    url = ""
    try:
        url = page.url
    except Exception:  # noqa: BLE001
        pass

    elements = [
        e
        for e in inventory(page)
        if "nav-narrate" not in str(e.get("selector") or "").lower()
        and "nav-narrate" not in str(e.get("id") or "").lower()
        and "navigator-chrome" not in str(e.get("testid") or "").lower()
    ]

    if step.kind == "USER_INPUT":
        # Phase A: pause for Client — never type visitor data while recording.
        return {
            "ok": False,
            "paused": True,
            "reason": "user_input",
            "prompt": step.live_question or step.label,
            "alias": step.alias,
            "context": {"url": url, "kind": "user_input"},
        }

    hint = step.action_hint or step.label or step.alias.replace("_", " ")
    target, ambiguous = find_action_target(elements, hint, step.alias)
    if ambiguous:
        return {
            "ok": False,
            "paused": True,
            "reason": "ambiguous_click",
            "prompt": f"Which control should I click for “{step.label}”?",
            "alias": step.alias,
            "candidates": [
                {
                    "index": i,
                    "label": (
                        el.get("label") or el.get("text") or el.get("aria_label") or ""
                    )[:80],
                    "tag": el.get("tag") or "",
                }
                for i, el in enumerate(ambiguous[:8])
            ],
            "context": {"url": url},
        }
    if target is None:
        return {
            "ok": False,
            "paused": True,
            "reason": "no_match",
            "prompt": (
                f"I could not find a control for “{step.label}”. "
                "Click it in the browser to record this step."
            ),
            "alias": step.alias,
            "context": {"url": url},
        }

    alias, css = prefer_selector(target)
    try:
        click_with_cursor(page, css)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
            "alias": alias,
            "selector": css,
        }

    return {
        "ok": True,
        "tool": "click_element",
        "alias": alias,
        "selector": css,
        "message": step.spoken or step.label,
    }


def element_by_index(elements: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    clickable = []
    for el in elements:
        if is_fillable(el):
            continue
        a, css = prefer_selector(el)
        if junk_record_reason(el, alias=a, selector=css):
            continue
        clickable.append(el)
    if 0 <= index < len(clickable):
        return clickable[index]
    return None
