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


def load_product_brief(
    product_id: str,
    *,
    site: str = "",
    base_url: str = "",
) -> str:
    """Return canonical markdown knowledge brief for product_id, or empty."""
    from navigator.knowledge.knowledge_merge import ensure_user_from_canonical
    from navigator.knowledge.product_ids import product_id_aliases

    for cid in product_id_aliases(product_id, site=site, base_url=base_url):
        ensure_user_from_canonical(cid)
        path = canonical_path(cid)
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
        alt = _ROOT / f"{cid.replace('-', '_')}.md"
        if alt.is_file():
            text = alt.read_text(encoding="utf-8").strip()
            if text:
                return text
    return ""


def save_product_brief(product_id: str, text: str) -> str:
    """Write canonical knowledge markdown for product_id. Returns stripped text."""
    return save_canonical_markdown(product_id, text)


def load_agent_context(product_id: str, *, site: str = "", base_url: str = "") -> str:
    """Bio (structured) + knowledge MD for planner / turn brain."""
    parts: list[str] = []
    bio_md = format_bio_markdown(
        load_bio(product_id, site=site, base_url=base_url)
    )
    if bio_md:
        parts.append(bio_md)
    knowledge = load_product_brief(product_id, site=site, base_url=base_url)
    if knowledge:
        parts.append(knowledge)
    # If no markdown brief, synthesize from bio so demos still get captured facts.
    if not knowledge and bio_md:
        parts.append(
            "## Demo briefing\nUse the company bio above when explaining screens. "
            "Prefer concrete product features over generic filler."
        )
    try:
        import yaml

        from navigator.client.content import resolve_topology_yaml

        topo_yaml = resolve_topology_yaml(
            product_id,
            yaml.safe_dump({"site": site, "base_url": base_url}) if (site or base_url) else "",
        )
        if not topo_yaml.strip() and not site and not base_url:
            topo_yaml = resolve_topology_yaml(product_id)
        if topo_yaml.strip():
            raw = yaml.safe_load(topo_yaml) or {}
            pages = raw.get("pages") if isinstance(raw, dict) else None
            if isinstance(pages, dict) and pages:
                lines = ["## Product map (explored)", f"{len(pages)} screens:"]
                for i, (pid, meta) in enumerate(pages.items()):
                    if i >= 20:
                        lines.append(f"- … +{len(pages) - 20} more")
                        break
                    if not isinstance(meta, dict):
                        continue
                    title = str(meta.get("title") or pid).strip()
                    url = str(meta.get("url") or "").strip()
                    # Prefer path label for identical brand titles.
                    from navigator.client.content import _page_label

                    label = _page_label(title, str(pid), url)
                    lines.append(f"- {label}: {url}")
                lines.append(
                    "Walk the site-graph playlist; use bio + this map for narration."
                )
                parts.append("\n".join(lines))
    except Exception:  # noqa: BLE001
        pass
    return "\n\n".join(parts).strip()


__all__ = [
    "load_product_brief",
    "save_product_brief",
    "load_agent_context",
    "load_knowledge_bundle",
]
