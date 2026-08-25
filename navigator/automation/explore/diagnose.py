"""Classify why an explore step failed. Deterministic — no LLM."""

from __future__ import annotations

import re
from typing import Any, Literal

from navigator.core.schemas import ToolResult

StuckKind = Literal[
    "not_found",
    "not_visible",
    "intercepted",
    "disabled",
    "detached",
    "timeout",
    "nav_stalled",
    "verify_failed",
    "unknown",
]

STUCK_KINDS: tuple[StuckKind, ...] = (
    "not_found",
    "not_visible",
    "intercepted",
    "disabled",
    "detached",
    "timeout",
    "nav_stalled",
    "verify_failed",
    "unknown",
)

# Playwright / tool detail → StuckKind. Order matters: more specific first.
_PATTERNS: tuple[tuple[re.Pattern[str], StuckKind], ...] = (
    (re.compile(r"intercepts?\s+pointer\s+events|another element would receive", re.I), "intercepted"),
    (re.compile(r"not\s+visible|outside of the viewport|obscured", re.I), "not_visible"),
    (re.compile(r"not\s+attached|detached\s+from\s+(the\s+)?DOM", re.I), "detached"),
    (re.compile(r"\bdisabled\b|not enabled|aria-disabled", re.I), "disabled"),
    (re.compile(r"strict mode violation|resolved to \d+ elements", re.I), "not_found"),
    (re.compile(r"waiting for (locator|selector)|no (node|element)|not found|TimeoutError", re.I), "not_found"),
    (re.compile(r"Timeout\s*\d+ms\s*exceeded|timed?\s*out", re.I), "timeout"),
)


def classify(
    result: ToolResult,
    *,
    verify_passed: bool | None = None,
    verify_actual: str = "",
    nav_stalled: bool = False,
) -> StuckKind:
    """Map a failed (or stalled) action to a StuckKind.

    `nav_stalled` wins when the caller already detected ok execute + unchanged
    URL/fingerprint — that case is invisible in ToolResult.detail alone.
    """
    if nav_stalled:
        return "nav_stalled"
    if result.ok and verify_passed is False:
        return "verify_failed"
    detail = (result.detail or "").strip()
    if not result.ok and detail:
        for pat, kind in _PATTERNS:
            if pat.search(detail):
                return kind
    if result.ok and verify_passed is False:
        return "verify_failed"
    if verify_actual and not result.ok:
        return "verify_failed"
    if not result.ok:
        return "unknown"
    return "unknown"


def looks_nav_stalled(
    *,
    fillable: bool,
    result_ok: bool,
    url_before: str,
    url_after: str,
    fp_before: Any,
    fp_after: Any,
) -> bool:
    """Click "succeeded" but page state did not change — the silent stall case."""
    if fillable or not result_ok:
        return False
    if fp_before is None or fp_after is None:
        return False
    return url_before == url_after and fp_before == fp_after
