"""Snapshot of what Playwright is showing — for spoken context."""

from __future__ import annotations

from playwright.sync_api import Page


def screen_snapshot(page: Page, *, max_chars: int = 900) -> str:
    """Compact url + title + visible text for the planner / agent."""
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    try:
        title = page.title() or ""
    except Exception:  # noqa: BLE001
        title = ""
    text = ""
    try:
        text = page.inner_text("body", timeout=1500) or ""
    except Exception:  # noqa: BLE001
        try:
            text = page.evaluate(
                "() => (document.body && document.body.innerText) || ''"
            ) or ""
        except Exception:  # noqa: BLE001
            text = ""
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return f"url={url}\ntitle={title}\nvisible={text}"
