"""Auto-build Product Map areas from explored flow semantics.

Groups flows by URL-path prefix (when present in step labels / purpose tags)
and tag overlap, then upserts through ProductMapStore. The live agent reads
these via `retrieve_context` → `relevant_areas`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import yaml as _yaml

from navigator.app.registry import Registry
from navigator.knowledge.context import ProductMapArea
from navigator.knowledge.product_map import ProductMapStore

_SLUG = re.compile(r"[^a-z0-9]+")


def sync_from_yaml(
    registry: Registry,
    product_id: str,
    yaml_text: str,
    *,
    product_name: str = "",
) -> list[ProductMapArea]:
    """Derive areas from `_meta.semantics` and upsert. Returns what was written."""
    raw = _yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        return []
    meta = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
    semantics = meta.get("semantics") if isinstance(meta.get("semantics"), dict) else {}
    if not semantics:
        return []

    flows: list[dict[str, Any]] = []
    for flow_id, entry in semantics.items():
        if not isinstance(entry, dict):
            continue
        purpose = str(entry.get("purpose") or "").strip()
        tags = entry.get("tags") if isinstance(entry.get("tags"), list) else []
        tag_set = {str(t).strip().lower() for t in tags if str(t).strip()}
        auto_name = str(entry.get("auto_name") or entry.get("name") or "").strip()
        flows.append(
            {
                "flow_id": str(flow_id),
                "purpose": purpose,
                "tags": tag_set,
                "name": auto_name or str(flow_id).replace("_", " "),
            }
        )
    if not flows:
        return []

    groups = _group_flows(flows)
    store = ProductMapStore(registry._conn)
    written: list[ProductMapArea] = []
    for area_id, members in groups.items():
        name = _area_name(area_id, members, product_name)
        purposes = [m["purpose"] for m in members if m["purpose"]]
        purpose = (
            purposes[0]
            if len(purposes) == 1
            else (
                f"Covers {', '.join(p.rstrip('.') for p in purposes[:3])}."
                if purposes
                else f"Area: {name}"
            )
        )
        cats = set()
        for m in members:
            cats |= m["tags"]
        area = ProductMapArea(
            product_id=product_id,
            area_id=area_id,
            name=name,
            purpose=purpose,
            related_flow_ids=[m["flow_id"] for m in members],
            related_chunk_ids=[],
            categories=cats,
        )
        store.upsert(area)
        written.append(area)
    return written


def _group_flows(flows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group by dominant tag, falling back to a single 'explored' area."""
    by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    untagged: list[dict[str, Any]] = []
    for f in flows:
        # Prefer a concrete product-area tag over generic ones.
        preferred = _preferred_tag(f["tags"])
        if preferred:
            by_tag[preferred].append(f)
        else:
            untagged.append(f)

    # Merge tiny tag groups (1 flow) into a neighbour that shares any tag, else
    # keep them — a singleton area is still better than burying the flow.
    groups: dict[str, list[dict[str, Any]]] = {}
    for tag, members in by_tag.items():
        groups[_slug(tag)] = members
    if untagged:
        if len(groups) == 1:
            next(iter(groups.values())).extend(untagged)
        else:
            groups["explored"] = groups.get("explored", []) + untagged
    return groups or {"explored": flows}


def _preferred_tag(tags: set[str]) -> str:
    skip = {"create", "send", "view", "edit", "delete", "new", "open", "click"}
    concrete = sorted(t for t in tags if t not in skip and len(t) > 2)
    return concrete[0] if concrete else (sorted(tags)[0] if tags else "")


def _area_name(area_id: str, members: list[dict[str, Any]], product_name: str) -> str:
    if area_id == "explored":
        return f"{product_name or 'Product'} overview".strip()
    return area_id.replace("-", " ").replace("_", " ").title()


def _slug(text: str) -> str:
    s = _SLUG.sub("-", text.strip().lower()).strip("-")
    return (s[:40] or "area")
