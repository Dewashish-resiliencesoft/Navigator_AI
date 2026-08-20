"""Load per-product agent briefs (swappable for multi-tenant demos)."""

from __future__ import annotations

from pathlib import Path

from navigator.knowledge.company_bio import format_bio_markdown, load_bio
from navigator.knowledge.knowledge_merge import (
    canonical_path,
    load_knowledge_bundle,
    save_canonical_markdown,
)

_ROOT = Path(__file__).resolve().parent / "products"


def load_product_brief(product_id: str) -> str:
    """Return canonical markdown knowledge brief for product_id, or empty."""
    from navigator.knowledge.knowledge_merge import ensure_user_from_canonical

    ensure_user_from_canonical(product_id)
    path = canonical_path(product_id)
    if not path.is_file():
        alt = _ROOT / f"{product_id.replace('-', '_')}.md"
        if alt.is_file():
            path = alt
        else:
            return ""
    return path.read_text(encoding="utf-8").strip()


def save_product_brief(product_id: str, text: str) -> str:
    """Write canonical knowledge markdown for product_id. Returns stripped text."""
    return save_canonical_markdown(product_id, text)


def load_agent_context(product_id: str) -> str:
    """Bio (structured) + knowledge MD for planner / turn brain."""
    parts: list[str] = []
    bio_md = format_bio_markdown(load_bio(product_id))
    if bio_md:
        parts.append(bio_md)
    knowledge = load_product_brief(product_id)
    if knowledge:
        parts.append(knowledge)
    try:
        from navigator.knowledge.topology import load_topology

        topo = load_topology(product_id)
        if topo.get("page_count"):
            parts.append(
                f"## Product map (automated)\n"
                f"{topo['page_count']} pages discovered. Use this as orientation only; "
                f"demo walkthroughs come from recorded flows."
            )
    except Exception:  # noqa: BLE001
        pass
    return "\n\n".join(parts).strip()


__all__ = [
    "load_product_brief",
    "save_product_brief",
    "load_agent_context",
    "load_knowledge_bundle",
]
