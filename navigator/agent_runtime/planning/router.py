"""Simple vs complex utterance routing — keep Flash off the hot path."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_BROWSER_VERBS = re.compile(
    r"\b("
    r"open|show|go to|navigate|click|find|compare|create|export|download|"
    r"filter|search|select|fill|submit|delete|update|change|switch|"
    r"figure out|fix|why isn't|doesn't work|not working"
    r")\b",
    re.I,
)

_SIMPLE_ACK = re.compile(
    r"^(yes|yeah|yep|ok|okay|sure|continue|go on|thanks|thank you|got it|"
    r"understood|mhm|mm hmm|right|correct|no problem)[.!?…]*$",
    re.I,
)


@dataclass(frozen=True)
class RouteDecision:
    route: Literal["live_direct", "orchestrator"]
    reason: str


def classify_utterance(text: str) -> RouteDecision:
    raw = (text or "").strip()
    if not raw:
        return RouteDecision("live_direct", "empty")
    if _SIMPLE_ACK.match(raw):
        return RouteDecision("live_direct", "acknowledgement")
    if len(raw.split()) <= 4 and not _BROWSER_VERBS.search(raw):
        if raw.endswith("?"):
            return RouteDecision("live_direct", "short_question")
        return RouteDecision("live_direct", "short_reply")
    if _BROWSER_VERBS.search(raw):
        return RouteDecision("orchestrator", "browser_task")
    if len(raw.split()) >= 8:
        return RouteDecision("orchestrator", "complex_instruction")
    return RouteDecision("live_direct", "conversational")
