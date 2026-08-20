"""Read-only automated product map (non-demo site topology).

Separate from site_graph_revisions — never published as a live walkthrough.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent / "products"


def _safe_id(product_id: str) -> str:
    return (product_id or "default").strip() or "default"


def topology_path(product_id: str) -> Path:
    return _ROOT / f"{_safe_id(product_id)}.topology.yaml"


def meta_path(product_id: str) -> Path:
    return _ROOT / f"{_safe_id(product_id)}.topology.meta.json"


def load_topology(product_id: str) -> dict[str, Any]:
    path = topology_path(product_id)
    if not path.is_file():
        return {"yaml": "", "updated_at": None, "page_count": 0}
    text = path.read_text(encoding="utf-8")
    updated_at = None
    page_count = 0
    meta = meta_path(product_id)
    if meta.is_file():
        import json

        try:
            raw = json.loads(meta.read_text(encoding="utf-8"))
            updated_at = raw.get("updated_at")
            page_count = int(raw.get("page_count") or 0)
        except Exception:  # noqa: BLE001
            pass
    if not page_count:
        try:
            data = yaml.safe_load(text) or {}
            pages = data.get("pages") if isinstance(data, dict) else None
            page_count = len(pages) if isinstance(pages, dict) else 0
        except Exception:  # noqa: BLE001
            page_count = 0
    return {"yaml": text, "updated_at": updated_at, "page_count": page_count}


def save_topology(product_id: str, yaml_text: str, *, page_count: int = 0) -> dict[str, Any]:
    import json

    path = topology_path(product_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml_text if yaml_text.endswith("\n") else yaml_text + "\n"
    path.write_text(body, encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat()
    if page_count <= 0:
        try:
            data = yaml.safe_load(body) or {}
            pages = data.get("pages") if isinstance(data, dict) else None
            page_count = len(pages) if isinstance(pages, dict) else 0
        except Exception:  # noqa: BLE001
            page_count = 0
    meta_path(product_id).write_text(
        json.dumps({"updated_at": now, "page_count": page_count, "source": "explore"})
        + "\n",
        encoding="utf-8",
    )
    return {"yaml": body, "updated_at": now, "page_count": page_count}
