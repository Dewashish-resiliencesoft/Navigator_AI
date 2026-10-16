"""DOM → semantic interactive surface for Live (compact) and Flash (detailed)."""

from __future__ import annotations

import re
from typing import Any

from navigator.automation.explore import perceive
from navigator.automation.explore.session import element_key


def semantic_id_for(el: dict[str, Any]) -> str:
    key = element_key(el)
    slug = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    return slug or "element"


def _element_record(el: dict[str, Any]) -> dict[str, Any]:
    label = (
        (el.get("text") or "").strip()
        or (el.get("aria_label") or "").strip()
        or (el.get("label") or "").strip()
        or (el.get("title") or "").strip()
    )
    return {
        "id": semantic_id_for(el),
        "type": el.get("role") or el.get("tag") or "element",
        "text": label[:80],
        "visible": True,
        "enabled": not el.get("disabled"),
        "fillable": bool(el.get("fillable")),
        "tag": el.get("tag") or "",
    }


def build_dom_state(page: Any, *, page_id: str = "", detailed: bool = False) -> dict[str, Any]:
    """Two representations: compact for Live, detailed for Flash."""
    url = ""
    title = ""
    try:
        url = page.url or ""
        title = page.title() or ""
    except Exception:  # noqa: BLE001
        pass

    elements: list[dict[str, Any]] = []
    try:
        raw = perceive.inventory(page)
        elements = [_element_record(el) for el in raw[:80 if detailed else 24]]
    except Exception as exc:  # noqa: BLE001
        print(f"[dom] inventory failed: {exc}", flush=True)

    visible_labels = [e["text"] for e in elements if e.get("text")][:12]

    live_context = {
        "page": page_id or "unknown",
        "url": url,
        "title": title,
        "visible_elements": visible_labels,
        "active_element": visible_labels[0] if visible_labels else "",
    }

    if detailed:
        return {
            **live_context,
            "elements": elements,
        }
    return live_context
