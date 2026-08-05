"""Replay a drafted flow and score whether it is safe to offer live.

Produces `_meta.validation[flow_id]` with pass_rate, risk_score, and a verdict.
Publish stays a human action (CLAUDE.md invariant #2 — published is per-revision).
The live agent only sees `ready` flows; `broken` / `needs_review` stay for the
Client dashboard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Sequence

from navigator.core.schemas import ToolCall, ToolResult

Verdict = Literal["ready", "needs_review", "broken"]

#: Destructive / financial language in purpose, tags, or step labels.
_DELETE = re.compile(r"\b(deletes?|deleting|deleted|removes?|removing|removed|destroys?)\b", re.I)
_SEND_PUBLISH = re.compile(
    r"\b(sends?|sending|sent|publishes?|publishing|published)\b", re.I
)
_FINANCIAL = re.compile(
    r"\b(pays?|paying|paid|payment|charges?|charging|charged|refunds?|refunding|"
    r"refunded|checkout|purchase|buy|billing\s*card|credit\s*card)\b",
    re.I,
)


@dataclass(frozen=True)
class ValidationResult:
    pass_rate: float
    failed_step_idxs: tuple[int, ...]
    risk_score: float
    verdict: Verdict
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "pass_rate": round(self.pass_rate, 3),
            "failed_step_idxs": list(self.failed_step_idxs),
            "risk_score": round(self.risk_score, 1),
            "verdict": self.verdict,
            "reason": self.reason,
        }


def risk_score(
    *,
    purpose: str = "",
    tags: Sequence[str] = (),
    step_descriptions: Sequence[str] = (),
    n_steps: int = 0,
    pass_rate: float = 0.0,
) -> float:
    """Higher = more dangerous to offer without a human looking."""
    blob = " ".join(
        [
            purpose,
            " ".join(str(t) for t in tags),
            " ".join(step_descriptions),
        ]
    )
    score = 0.0
    if _DELETE.search(blob):
        score += 50
    if _SEND_PUBLISH.search(blob):
        score += 40
    if _FINANCIAL.search(blob):
        score += 100
    if n_steps <= 1:
        score += 20
    score -= pass_rate * 30
    return max(0.0, score)


def verdict_for(
    *,
    purpose: str = "",
    tags: Sequence[str] = (),
    step_descriptions: Sequence[str] = (),
    n_steps: int = 0,
    pass_rate: float = 0.0,
    failed_step_idxs: Sequence[int] = (),
) -> ValidationResult:
    """Compute risk + verdict. Financial / destructive can never be `ready`."""
    score = risk_score(
        purpose=purpose,
        tags=tags,
        step_descriptions=step_descriptions,
        n_steps=n_steps,
        pass_rate=pass_rate,
    )
    blob = " ".join([purpose, " ".join(str(t) for t in tags), " ".join(step_descriptions)])
    financial = bool(_FINANCIAL.search(blob))
    destructive = bool(_DELETE.search(blob))

    if pass_rate < 0.5:
        v: Verdict = "broken"
        reason = f"pass_rate {pass_rate:.2f} below 0.5"
    elif financial or destructive:
        v = "needs_review"
        reason = "financial" if financial else "destructive"
        reason = f"{reason} flow can never be ready"
    elif score < 30 and pass_rate >= 0.9:
        v = "ready"
        reason = "low risk, high pass rate"
    elif pass_rate < 0.9:
        v = "needs_review"
        reason = f"pass_rate {pass_rate:.2f} below 0.9"
    else:
        v = "needs_review"
        reason = f"risk_score {score:.0f} ≥ 30"

    return ValidationResult(
        pass_rate=pass_rate,
        failed_step_idxs=tuple(failed_step_idxs),
        risk_score=score,
        verdict=v,
        reason=reason,
    )


def validate_flow(
    *,
    steps: Sequence[ToolCall],
    page: Any,
    graph: Any,
    page_id: str,
    execute: Callable[..., tuple[ToolResult, str]],
    verify: Callable[..., Any],
    purpose: str = "",
    tags: Sequence[str] = (),
    step_descriptions: Sequence[str] = (),
) -> ValidationResult:
    """Replay every step; score from outcomes + semantic risk."""
    if not steps:
        return verdict_for(
            purpose=purpose,
            tags=tags,
            step_descriptions=step_descriptions,
            n_steps=0,
            pass_rate=0.0,
            failed_step_idxs=(),
        )

    failed: list[int] = []
    current_page = page_id
    for i, call in enumerate(steps):
        result, current_page = execute(page, graph, current_page, call)
        ok = result.ok
        expects = getattr(call, "expects", None)
        if ok and expects is not None:
            vr = verify(page, graph, current_page, expects)
            ok = bool(getattr(vr, "passed", False))
        if not ok:
            failed.append(i)

    n = len(steps)
    rate = (n - len(failed)) / n
    return verdict_for(
        purpose=purpose,
        tags=tags,
        step_descriptions=step_descriptions,
        n_steps=n,
        pass_rate=rate,
        failed_step_idxs=failed,
    )


def is_offerable(validation: dict[str, Any] | None) -> bool:
    """True when the live agent may rank this flow.

    Missing validation = grandfathered manual / pre-validator flow.
    Explicit broken / needs_review = never offered live.
    """
    if not validation:
        return True
    return str(validation.get("verdict") or "") == "ready"
