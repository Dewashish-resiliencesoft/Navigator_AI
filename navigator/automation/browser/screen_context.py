"""Snapshot of what Playwright is showing — for spoken context."""

from __future__ import annotations

from playwright.sync_api import Page


def planner_screen(page: Page, *, graph: object = None, page_id: str = "", max_chars: int = 900) -> str:
    """On-screen section plus nearby off-screen items. Not the full page map.

    Falls back to the compact text snapshot when the viewport cannot be read.
    """
    from navigator.automation.browser.viewport_awareness import measure_viewport

    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    try:
        title = page.title() or ""
    except Exception:  # noqa: BLE001
        title = ""
    awareness = measure_viewport(page, graph=graph, page_id=page_id)
    summary = awareness.summary()
    if summary:
        text = f"url={url}\ntitle={title}\n{summary}"
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        return text
    return screen_snapshot(page, max_chars=max_chars)


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
