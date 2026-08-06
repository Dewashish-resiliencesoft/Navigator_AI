"""Load per-product agent briefs (swappable for multi-tenant demos)."""

from __future__ import annotations

from pathlib import Path

from navigator.knowledge.company_bio import format_bio_markdown, load_bio

_ROOT = Path(__file__).resolve().parent / "products"


def load_product_brief(product_id: str) -> str:
    """Return markdown knowledge brief for product_id, or empty if missing."""
    path = _ROOT / f"{product_id}.md"
    if not path.is_file():
        # Common alias: site graph `site: acme-corp` → acme_corp.md if present.
        alt = _ROOT / f"{product_id.replace('-', '_')}.md"
        if alt.is_file():
            path = alt
        else:
            return ""
    return path.read_text(encoding="utf-8").strip()


def save_product_brief(product_id: str, text: str) -> str:
    """Write knowledge markdown for product_id. Returns stripped text."""
    safe = (product_id or "default").strip() or "default"
    path = _ROOT / f"{safe}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = text if (text or "").endswith("\n") else (text or "") + ("\n" if text else "")
    path.write_text(body, encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def load_agent_context(product_id: str) -> str:
    """Bio (structured) + knowledge MD for planner / turn brain."""
    parts: list[str] = []
    bio_md = format_bio_markdown(load_bio(product_id))
    if bio_md:
        parts.append(bio_md)
    knowledge = load_product_brief(product_id)
    if knowledge:
        parts.append(knowledge)
    return "\n\n".join(parts).strip()
