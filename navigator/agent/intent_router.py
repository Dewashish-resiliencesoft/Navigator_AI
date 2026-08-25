"""State-aware intent routing — rules first, embeddings second, LLM last."""

from __future__ import annotations

import re

from navigator.agent.brain_decision import BrainDecision, BrainIntent
from navigator.agent.end_policy import is_goodbye
from navigator.knowledge.flow_triggers import match_flow_triggers

_CONTINUE = frozenset({"ok", "continue", "go on", "yes", "sure"})
_AFFIRM = frozenset(
    {"yes", "yeah", "yep", "sure", "ok", "okay", "please", "go ahead", "do it",
     "that one", "correct", "right", "exactly", "the first", "the second"}
)
_NEGATE = frozenset(
    {"no", "nope", "nah", "not that", "never mind", "nevermind", "cancel",
     "skip", "don't", "do not"}
)

_HESITATION = re.compile(
    r"\b(um+|uh+|wait|hold on|pause|stop)\b",
    re.I,
)


def route_intent(
    *,
    utterance: str,
    phase: str,
    allowed: frozenset[BrainIntent] | None = None,
) -> BrainDecision:
    """Cheap cascade before retrieval / turn-brain."""
    text = (utterance or "").strip()
    low = text.lower()
    allowed = allowed or frozenset(
        {
            "continue",
            "answer",
            "run_flow",
            "detour",
            "handoff",
            "end",
            "clarify",
            "goodbye",
            "affirm",
            "negate",
            "correction",
            "unknown",
        }
    )

    if not text:
        if "continue" in allowed:
            return BrainDecision(
                intent="continue",
                branch="continuation",
                detail="silence",
                router="rules",
            )
        return BrainDecision(intent="unknown", router="rules", detail="empty")

    if is_goodbye(text) and "goodbye" in allowed:
        return BrainDecision(
            intent="goodbye",
            branch="ended",
            detail="goodbye detected",
            router="rules",
        )

    if low in _CONTINUE or low in {"yeah", "yep", "thanks", "thank you"}:
        if phase == "awaiting_resume" and "affirm" in allowed:
            return BrainDecision(
                intent="affirm",
                branch="resume_confirm",
                detail="affirm/continue",
                router="rules",
            )
        if "continue" in allowed:
            return BrainDecision(
                intent="continue",
                branch="continuation",
                detail="continue token",
                router="rules",
            )

    if low in _AFFIRM or any(low == w or low.startswith(w + " ") for w in _AFFIRM):
        if "affirm" in allowed:
            return BrainDecision(
                intent="affirm",
                branch="resume_confirm",
                detail="affirm",
                router="rules",
            )

    if low in _NEGATE or any(low.startswith(w) for w in _NEGATE):
        if "negate" in allowed:
            return BrainDecision(
                intent="negate",
                branch="declined",
                detail="negate",
                router="rules",
            )

    if _HESITATION.search(text) and "clarify" in allowed:
        return BrainDecision(
            intent="clarify",
            branch="clarify",
            detail="hesitation/stop",
            router="rules",
        )

    return BrainDecision(
        intent="unknown",
        branch="question",
        detail="needs retrieval",
        router="rules",
    )


def route_flow_from_triggers(
    utterance: str,
    *,
    graph,
    page_id: str,
) -> BrainDecision | None:
    hit = match_flow_triggers(utterance, graph=graph, page_id=page_id)
    if hit is None:
        return None
    flow_id, _page = hit
    return BrainDecision(
        intent="run_flow",
        flow_id=flow_id,
        confidence=1.0,
        branch="flow_executed",
        detail=f"trigger match {flow_id}",
        router="triggers",
    )
