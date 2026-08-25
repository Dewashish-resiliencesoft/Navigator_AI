"""Tier 2: constrained live fallback when retrieval finds nothing.

Default OFF per product (`CallDeps.tier2_enabled`). When on, a single propose →
guardrail → act cycle may run. Destructive targets are refused always — the
guardrail is a hard rule, not a confidence threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from navigator.automation.explore.guardrail import GuardrailVerdict, classify_action
from navigator.core.schemas import ToolCall


@dataclass(frozen=True)
class Tier2Proposal:
    element: dict[str, Any]
    call: ToolCall
    spoken: str


@dataclass(frozen=True)
class Tier2Outcome:
    branch: str  # tier2_attempted | tier2_refused
    spoken: str
    detail: str
    call: ToolCall | None = None
    element: dict[str, Any] | None = None


def run_tier2(
    *,
    utterance: str,
    propose: Callable[..., dict | Tier2Proposal | None],
    classify: Callable[..., GuardrailVerdict] | None = None,
) -> Tier2Outcome | None:
    """One guarded attempt. None → caller should hand off as before."""
    try:
        raw = propose(utterance=utterance)
    except Exception as exc:  # noqa: BLE001
        print(f"[tier2] propose failed: {exc}", flush=True)
        return None
    if raw is None:
        return None

    if isinstance(raw, Tier2Proposal):
        element, call, spoken = raw.element, raw.call, raw.spoken
    else:
        element = dict(raw.get("element") or {})
        call = raw.get("call")
        spoken = str(raw.get("spoken") or "").strip()
        if call is None:
            return None

    gate = classify or classify_action
    try:
        verdict = gate(element)
    except Exception as exc:  # noqa: BLE001
        verdict = GuardrailVerdict(True, f"guardrail error: {exc}", "fail_closed")

    if not isinstance(verdict, GuardrailVerdict):
        verdict = GuardrailVerdict(True, "invalid guardrail verdict", "fail_closed")

    if verdict.flagged:
        return Tier2Outcome(
            branch="tier2_refused",
            spoken=(
                spoken
                or "I shouldn't interact with that during a live demo — "
                "I'll leave it for a human."
            ),
            detail=f"refused: {verdict.reason} ({verdict.source})",
            call=None,
            element=element,
        )

    return Tier2Outcome(
        branch="tier2_attempted",
        spoken=spoken or "Let me try opening that for you.",
        detail=f"safe action via {verdict.source}: {verdict.reason}",
        call=call,
        element=element,
    )
