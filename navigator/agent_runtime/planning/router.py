"""Realtime utterance routing — keeps Flash and Playwright off the hot path.

Three routes:
  BACKCHANNEL   — user is still talking / short filler; Live may optionally
                  generate a very brief natural acknowledgement or stay silent.
  ANSWER        — Live answers directly from its context; no browser needed.
  TASK_HANDOFF  — complex work delegated to the orchestrator; Live emits an
                  immediate natural acknowledgement while Flash/Playwright run.

Gemini Live controls *what* to say; the router controls *whether* to delegate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

_BROWSER_VERBS = re.compile(
    r"\b("
    r"open|show|go to|navigate|click|find|compare|create|export|download|"
    r"filter|search|select|fill|submit|delete|update|change|switch|"
    r"figure out|fix|why isn't|doesn't work|not working|walk me through|"
    r"demonstrate|run|execute|set up|configure|add|remove|send|start"
    r")\b",
    re.I,
)

_MULTI_STEP = re.compile(
    r"\b(and (then|also)|after that|then|followed by|finally)\b",
    re.I,
)

# Utterances that are pure filler — Live may backchannel or stay silent.
_BACKCHANNEL_PATTERN = re.compile(
    r"^(um+|uh+|hmm+|mm+|ah+|er+|like|so|and|but|wait|okay so|right so|"
    r"let me|let's see|interesting|cool|nice|wow|oh|oh okay|oh right|got it|"
    r"yes|yeah|yep|ok|okay|sure|continue|go on|thanks|thank you|"
    r"understood|mhm|mm.?hmm|right|correct|no problem|sounds good|"
    r"alright|makes sense|i see)[.!?…,]*$",
    re.I,
)

# Questions Live can answer without touching the browser.
_CONVERSATIONAL_Q = re.compile(
    r"\b(what (does|is|are|can)|how (does|do|can)|tell me about|"
    r"explain|describe|can (it|this|you)|does it|is there|"
    r"which (plan|tier|feature)|who (uses|is)|why (does|is|would))\b",
    re.I,
)


@dataclass(frozen=True)
class RouteDecision:
    route: Literal["backchannel", "answer", "task_handoff"]
    reason: str
    # Hint for the immediate ack when route == task_handoff
    ack_hint: str = ""


def classify_utterance(text: str, *, agent_working: bool = False) -> RouteDecision:
    """Classify one user utterance into BACKCHANNEL / ANSWER / TASK_HANDOFF.

    ``agent_working`` — True when the orchestrator is already executing a task.
    In that case we prefer BACKCHANNEL over ANSWER to avoid interrupting flow.
    """
    raw = (text or "").strip()
    if not raw:
        return RouteDecision("backchannel", "empty")

    words = raw.split()

    # Pure filler / ack
    if _BACKCHANNEL_PATTERN.match(raw):
        return RouteDecision("backchannel", "filler_or_ack")

    # Browser action required
    if _BROWSER_VERBS.search(raw):
        multi = bool(_MULTI_STEP.search(raw))
        return RouteDecision(
            "task_handoff",
            "browser_task_multi" if multi else "browser_task",
            ack_hint=raw,
        )

    # Long complex instruction even without explicit browser verb
    if len(words) >= 10:
        return RouteDecision("task_handoff", "complex_instruction", ack_hint=raw)

    # Short conversational question
    if _CONVERSATIONAL_Q.search(raw) and not agent_working:
        return RouteDecision("answer", "conversational_question")

    # Anything short while agent is working → backchannel only
    if agent_working and len(words) <= 6:
        return RouteDecision("backchannel", "agent_busy_short")

    # Default: answer from Live context
    return RouteDecision("answer", "conversational")
