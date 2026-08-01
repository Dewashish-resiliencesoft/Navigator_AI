"""Load per-product agent briefs (swappable for multi-tenant demos)."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent / "products"


def load_product_brief(product_id: str) -> str:
    """Return markdown brief for product_id, or empty if missing."""
    path = _ROOT / f"{product_id}.md"
    if not path.is_file():
        # Common alias: site graph `site: resiliohub` → same file.
        alt = _ROOT / f"{product_id.replace('-', '_')}.md"
        if alt.is_file():
            path = alt
        else:
            return ""
    return path.read_text(encoding="utf-8").strip()
