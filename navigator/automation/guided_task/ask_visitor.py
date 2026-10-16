"""Propose a live USER_INPUT question from Client prompt + pause screenshot.

Screenshot only when the Client submits “Ask visitor this” — never continuous.
"""

from __future__ import annotations

from typing import Any


_SYSTEM = """You help a Client author a live product demo script.
They paused recording on a screen and typed what the End User (visitor) should be asked.
Return ONE short spoken question the demo host should ask the visitor (max 140 chars).
No quotes, no markdown, no preamble. If the Client prompt is already a good question, polish lightly."""


def capture_page_png(page: Any) -> bytes:
    """Viewport PNG; empty bytes on failure."""
    try:
        return page.screenshot(type="png", full_page=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[guided-ask] screenshot failed: {exc}", flush=True)
        return b""


def propose_live_question(page: Any, client_prompt: str) -> str:
    """Build live_question from Client text + optional pause screenshot."""
    prompt = " ".join((client_prompt or "").split()).strip()
    if not prompt:
        return "Could you fill this in?"

    png = capture_page_png(page)
    if not png:
        return _fallback_question(prompt)

    try:
        from navigator.agent.providers import get_provider

        provider = get_provider()
        user = (
            f"Client note (what to ask the visitor):\n{prompt}\n\n"
            "Screen is attached. Draft the spoken question only."
        )
        raw = provider.complete_with_image(_SYSTEM, user, png).strip()
        line = _clean_question(raw) or _fallback_question(prompt)
        print(f"[guided-ask] vision question: {line!r}", flush=True)
        return line
    except Exception as exc:  # noqa: BLE001
        print(f"[guided-ask] vision skipped ({exc}); using Client text", flush=True)
        return _fallback_question(prompt)


def _clean_question(raw: str) -> str:
    line = (raw or "").strip().strip('"').strip("'")
    if line.startswith("```"):
        line = line.strip("`").strip()
    line = " ".join(line.split())
    if len(line) > 160:
        line = line[:157].rstrip() + "…"
    if line and line[-1] not in ".?!":
        line += "?"
    return line


def _fallback_question(prompt: str) -> str:
    p = prompt.strip()
    if "?" in p:
        return p if len(p) <= 160 else p[:157].rstrip() + "…?"
    return f"{p.rstrip('.')}?" if len(p) < 140 else (p[:137].rstrip() + "…?")
