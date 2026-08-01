"""Structured company bio for ops console → agent context."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent / "products"

DEFAULT_BIO_FIELDS: list[dict[str, str]] = [
    {"key": "company_name", "label": "Company name", "value": ""},
    {"key": "owner", "label": "Owner / leadership", "value": ""},
    {"key": "products", "label": "Products", "value": ""},
    {"key": "about", "label": "What the company is about", "value": ""},
    {"key": "website", "label": "Website", "value": ""},
    {"key": "industry", "label": "Industry", "value": ""},
]


def _bio_path(product_id: str) -> Path:
    safe = (product_id or "default").strip() or "default"
    return _ROOT / f"{safe}.bio.json"


def _slug_key(label: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (label or "").strip().lower()).strip("_")
    return (s[:40] or "field")


def default_bio() -> dict[str, Any]:
    return {"fields": [dict(f) for f in DEFAULT_BIO_FIELDS]}


def load_bio(product_id: str) -> dict[str, Any]:
    path = _bio_path(product_id)
    if not path.is_file():
        # try hyphen/underscore alias
        alt = _ROOT / f"{product_id.replace('-', '_')}.bio.json"
        path = alt if alt.is_file() else path
    if not path.is_file():
        return default_bio()
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = data.get("fields")
    if not isinstance(fields, list) or not fields:
        return default_bio()
    cleaned: list[dict[str, str]] = []
    for raw in fields:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or raw.get("key") or "Field").strip()
        key = str(raw.get("key") or _slug_key(label)).strip() or _slug_key(label)
        cleaned.append(
            {"key": key, "label": label, "value": str(raw.get("value") or "")}
        )
    return {"fields": cleaned} if cleaned else default_bio()


def save_bio(product_id: str, bio: dict[str, Any]) -> dict[str, Any]:
    fields_in = bio.get("fields") if isinstance(bio, dict) else None
    if not isinstance(fields_in, list):
        raise ValueError("bio.fields must be a list")
    fields: list[dict[str, str]] = []
    for raw in fields_in:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or "").strip()
        if not label:
            continue
        key = str(raw.get("key") or _slug_key(label)).strip() or _slug_key(label)
        fields.append(
            {"key": key, "label": label, "value": str(raw.get("value") or "")}
        )
    if not fields:
        raise ValueError("bio needs at least one field")
    out = {"fields": fields}
    path = _bio_path(product_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def format_bio_markdown(bio: dict[str, Any] | None) -> str:
    if not bio:
        return ""
    fields = bio.get("fields") or []
    lines = ["## Company bio"]
    for f in fields:
        if not isinstance(f, dict):
            continue
        val = str(f.get("value") or "").strip()
        if not val:
            continue
        label = str(f.get("label") or f.get("key") or "Field").strip()
        lines.append(f"- **{label}:** {val}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)
