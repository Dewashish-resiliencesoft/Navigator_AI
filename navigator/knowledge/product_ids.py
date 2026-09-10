"""Resolve product_id aliases when explore artifacts use host slug vs registry id."""

from __future__ import annotations

from urllib.parse import urlparse


def product_id_aliases(
    product_id: str,
    *,
    site: str = "",
    base_url: str = "",
) -> list[str]:
    """Ordered ids to try for bio / knowledge / topology files."""
    out: list[str] = []
    for cid in (
        (product_id or "").strip(),
        (site or "").strip(),
        (product_id or "").replace("-", "_").strip(),
    ):
        if cid and cid not in out:
            out.append(cid)
    host = (urlparse((base_url or "").strip()).hostname or "").lower()
    if host:
        slug = host.split(".")[0]
        if slug and slug not in out and slug not in {"www", "app", "api"}:
            out.append(slug)
        # drop leading www.
        parts = host.split(".")
        if len(parts) >= 2:
            brand = parts[-2] if parts[-1] in {"com", "io", "ai", "app", "net", "org"} else parts[0]
            if brand and brand not in out and brand not in {"www"}:
                out.append(brand)
    return out
