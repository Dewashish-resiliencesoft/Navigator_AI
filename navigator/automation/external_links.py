"""Off-product links: skip in explore, disclaim in live demos.

Explore must not drive the Watch bot to another origin — keep mapping the
Client's app. Live demos speak once that external destinations are for the
prospect to check on their own.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

#: Spoken when a demo click would leave the product origin.
EXTERNAL_LINK_SPOKEN = (
    "That link goes outside the product — you'll want to check that on your "
    "own; it's not part of this demo."
)


def url_origin(url: str) -> str:
    """``scheme://host`` lowercased, or empty when not absolute http(s)."""
    p = urlparse((url or "").strip())
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}".lower()


def is_external_href(
    href: str,
    product_base: str,
    *,
    page_url: str = "",
) -> bool:
    """True when ``href`` resolves off the product ``base_url`` origin."""
    raw = (href or "").strip()
    if not raw or raw == "#":
        return False
    low = raw.lower()
    if low.startswith(("mailto:", "tel:", "javascript:", "data:")):
        return True
    prod = url_origin(product_base)
    if not prod:
        return False
    base = (page_url or product_base).strip() or product_base
    absolute = urljoin(base, raw)
    p = urlparse(absolute)
    if p.scheme not in ("http", "https"):
        return True
    return url_origin(absolute) != prod


def is_external_url(url: str, product_base: str) -> bool:
    """True when the browser is on a different origin than the product."""
    prod = url_origin(product_base)
    if not prod:
        return False
    cur = url_origin(url)
    return bool(cur and cur != prod)


def external_href_reason(
    href: str,
    product_base: str,
    *,
    page_url: str = "",
) -> str | None:
    """Short reason for logs, or None when in-product."""
    raw = (href or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if low.startswith("mailto:"):
        return "mailto link"
    if low.startswith("tel:"):
        return "tel link"
    if low.startswith("javascript:"):
        return "javascript link"
    if is_external_href(raw, product_base, page_url=page_url):
        return "off-product URL"
    return None


def element_is_external(
    el: dict[str, Any],
    product_base: str,
    *,
    page_url: str = "",
) -> str | None:
    """Skip reason for an inventory element, or None."""
    href = str(el.get("href") or "")
    reason = external_href_reason(href, product_base, page_url=page_url)
    if reason:
        return reason
    target = str(el.get("target") or "").lower()
    if target == "_blank" and href.strip() and href != "#":
        if is_external_href(href, product_base, page_url=page_url):
            return "external new-tab link"
    return None


def revert_external_navigation(page: Any, *, product_base: str = "") -> bool:
    """Return to in-product after an accidental off-origin navigation."""
    try:
        page.go_back(timeout=8000)
        return True
    except Exception:  # noqa: BLE001
        pass
    base = (product_base or "").strip()
    if not base:
        return False
    try:
        page.goto(base, wait_until="domcontentloaded", timeout=60_000)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[external] revert failed: {exc}", flush=True)
        return False
