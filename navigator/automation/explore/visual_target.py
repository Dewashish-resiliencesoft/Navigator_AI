"""VLM fallback: locate a control by looking at the screenshot.

Last resort for `not_found` / `detached` after CSS alternatives fail. A working
selector is always cheaper, so this runs after `alternate_selector`.

Security: a VLM-resolved click is a click on an element the guardrail never saw
by selector. After resolving coordinates we re-inventory, find the element at
that point, and run `classify_action` before acting — same rule as
`dismiss_overlay`. Fail-closed: no provider / unparseable reply → no click.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from navigator.automation.explore.guardrail import classify_action
from navigator.automation.explore import perceive
from navigator.automation.record import prefer_selector

_PROMPT = """You are looking at a product UI screenshot.

Find the interactive control described below and reply with JSON only:
{{"x": <int 0-1000>, "y": <int 0-1000>, "found": true}}

Coordinates are normalized: (0,0) is top-left, (1000,1000) is bottom-right of
the visible viewport. If the control is not visible, reply:
{{"found": false}}

Control to find: {target}
"""

_COORD = re.compile(
    r'"?found"?\s*:\s*true.*?"?x"?\s*:\s*(\d+).*?"?y"?\s*:\s*(\d+)',
    re.I | re.S,
)


@dataclass(frozen=True)
class LocateHit:
    alias: str
    css: str
    el: dict[str, Any]
    x_norm: int
    y_norm: int


def parse_coords(raw: str) -> tuple[int, int] | None:
    """Normalized (x, y) in 0..1000, or None when unparseable / not found."""
    text = (raw or "").strip()
    if not text:
        return None
    if re.search(r'"?found"?\s*:\s*false', text, re.I):
        return None
    # Prefer JSON-ish scrape; fall back to regex.
    import json

    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data.get("found") is not False:
            try:
                x, y = int(data["x"]), int(data["y"])
            except (KeyError, TypeError, ValueError):
                x = y = -1
            if 0 <= x <= 1000 and 0 <= y <= 1000:
                return x, y
    m = _COORD.search(text)
    if not m:
        return None
    x, y = int(m.group(1)), int(m.group(2))
    if 0 <= x <= 1000 and 0 <= y <= 1000:
        return x, y
    return None


def element_at_point(
    elements: list[dict[str, Any]],
    *,
    x_norm: int,
    y_norm: int,
    viewport: tuple[int, int] | None = None,
) -> dict[str, Any] | None:
    """Pick the inventory element whose box contains the normalized point.

    Inventory entries may carry `box: {x,y,w,h}` in CSS pixels. Without boxes
    we cannot safely map a VLM point → element, so return None (fail-closed).
    """
    if not elements:
        return None
    vw, vh = viewport or (1280, 720)
    px = (x_norm / 1000.0) * vw
    py = (y_norm / 1000.0) * vh
    hits: list[tuple[float, dict[str, Any]]] = []
    for el in elements:
        box = el.get("box")
        if not isinstance(box, dict):
            continue
        try:
            x, y, w, h = float(box["x"]), float(box["y"]), float(box["w"]), float(box["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if x <= px <= x + w and y <= py <= y + h:
            area = max(1.0, w * h)
            hits.append((area, el))
    if not hits:
        return None
    # Smallest containing box wins (more specific control).
    hits.sort(key=lambda pair: pair[0])
    return hits[0][1]


def locate(
    *,
    page: Any,
    target: str,
    ask_vision: Callable[[str, str], str] | None,
    guard_judge: Callable[[str], str] | None,
    is_allowed: Callable[[dict[str, Any], str], bool],
    inventory: Callable[[Any], list[dict[str, Any]]] | None = None,
    screenshot: Callable[[Any], str] | None = None,
    viewport: tuple[int, int] | None = None,
) -> LocateHit | None:
    """Resolve a control via VLM + guardrail. None = do not click."""
    if ask_vision is None:
        return None
    inv = inventory or perceive.inventory
    shot_fn = screenshot or (lambda p: perceive.screenshot_b64(p))
    b64 = ""
    try:
        b64 = shot_fn(page) or ""
    except Exception as exc:  # noqa: BLE001
        print(f"[visual_target] screenshot failed: {exc}", flush=True)
        return None
    if not b64:
        return None

    try:
        raw = ask_vision(_PROMPT.format(target=target[:200]), b64)
    except Exception as exc:  # noqa: BLE001
        print(f"[visual_target] vision failed: {exc}", flush=True)
        return None

    coords = parse_coords(raw)
    if coords is None:
        return None
    x_norm, y_norm = coords

    elements = inv(page)
    el = element_at_point(elements, x_norm=x_norm, y_norm=y_norm, viewport=viewport)
    if el is None:
        return None

    alias, css = prefer_selector(el)
    if not is_allowed(el, css):
        verdict = classify_action(el, judge=guard_judge)
        if verdict.flagged:
            print(
                f"[visual_target] guardrail blocked {alias}: {verdict.reason}",
                flush=True,
            )
            return None

    return LocateHit(alias=alias, css=css, el=el, x_norm=x_norm, y_norm=y_norm)


def click_hit(page: Any, hit: LocateHit) -> bool:
    """Click via mouse at the resolved point. False on any Playwright error."""
    try:
        size = page.viewport_size or {"width": 1280, "height": 720}
        vw, vh = int(size["width"]), int(size["height"])
        page.mouse.click(int(hit.x_norm / 1000.0 * vw), int(hit.y_norm / 1000.0 * vh))
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[visual_target] click failed: {exc}", flush=True)
        return False
