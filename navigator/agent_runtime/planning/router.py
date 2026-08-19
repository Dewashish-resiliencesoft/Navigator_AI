"""Utterance routing — keeps Flash and Playwright off the realtime voice path.

Three canonical routes (use the module constants, not bare strings):

  ROUTE_BACKCHANNEL   user is still talking / pure filler — Live may optionally
                      emit a very brief natural acknowledgement or stay silent.

  ROUTE_ANSWER        Live answers directly from its own context; no browser.

  ROUTE_TASK_HANDOFF  complex work delegated to the orchestrator; Live emits
                      an immediate natural acknowledgement while Flash/Playwright run.

The router CONTROLS WHEN to delegate.
Gemini Live controls WHAT to say in all three cases.

Backward-compatibility notes
────────────────────────────
The old two-way router used ``"live_direct"`` and ``"orchestrator"``.
Those string values are now aliases; callers that compared against them still
work correctly because ROUTE_TASK_HANDOFF == "task_handoff" (not "orchestrator").
Any caller that still checks ``route == "orchestrator"`` must be updated to use
``route == ROUTE_TASK_HANDOFF`` — see orchestrator.py and bridge.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# ---- canonical route constants -------------------------------------------
# Use these everywhere; never compare against bare string literals.
ROUTE_BACKCHANNEL: Literal["backchannel"] = "backchannel"
ROUTE_ANSWER: Literal["answer"] = "answer"
ROUTE_TASK_HANDOFF: Literal["task_handoff"] = "task_handoff"

Route = Literal["backchannel", "answer", "task_handoff"]

# ---- matching patterns ---------------------------------------------------

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

# Pure filler — Live may backchannel or stay silent.
_BACKCHANNEL_PATTERN = re.compile(
    r"^(um+|uh+|hmm+|mm+|ah+|er+|like|so|and|but|wait|okay so|right so|"
    r"let me|let's see|interesting|cool|nice|wow|oh|oh okay|oh right|got it|"
    r"yes|yeah|yep|ok|okay|sure|continue|go on|thanks|thank you|"
    r"understood|mhm|mm.?hmm|right|correct|no problem|sounds good|"
    r"alright|makes sense|i see)[.!?…,]*$",
    re.I,
)

# Questions Live can answer directly without touching the browser.
_CONVERSATIONAL_Q = re.compile(
    r"\b(what (does|is|are|can)|how (does|do|can)|tell me about|"
    r"explain|describe|can (it|this|you)|does it|is there|"
    r"which (plan|tier|feature)|who (uses|is)|why (does|is|would))\b",
    re.I,
)


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    reason: str
    # Contextual hint for the immediate acknowledgement when route == ROUTE_TASK_HANDOFF.
    # Empty for other routes.
    ack_hint: str = field(default="")


def classify_utterance(
    text: str,
    *,
    agent_working: bool = False,
) -> RouteDecision:
    """Classify one user utterance into BACKCHANNEL / ANSWER / TASK_HANDOFF.

    ``agent_working`` — True when the orchestrator is already executing a task.
    Short utterances while working are treated as backchannels so they don't
    spawn a new task while the current one is still running.
    """
    raw = (text or "").strip()
    if not raw:
        return RouteDecision(ROUTE_BACKCHANNEL, "empty")

    words = raw.split()

    # Pure filler / ack
    if _BACKCHANNEL_PATTERN.match(raw):
        return RouteDecision(ROUTE_BACKCHANNEL, "filler_or_ack")

    # Browser action required
    if _BROWSER_VERBS.search(raw):
        multi = bool(_MULTI_STEP.search(raw))
        return RouteDecision(
            ROUTE_TASK_HANDOFF,
            "browser_task_multi" if multi else "browser_task",
            ack_hint=raw,
        )

    # Long complex instruction even without explicit browser verb
    if len(words) >= 10:
        return RouteDecision(ROUTE_TASK_HANDOFF, "complex_instruction", ack_hint=raw)

    # Short conversational question
    if _CONVERSATIONAL_Q.search(raw) and not agent_working:
        return RouteDecision(ROUTE_ANSWER, "conversational_question")

    # Short utterance while agent is working → backchannel only
    if agent_working and len(words) <= 6:
        return RouteDecision(ROUTE_BACKCHANNEL, "agent_busy_short")

    # Default: Live answers from its own context
    return RouteDecision(ROUTE_ANSWER, "conversational")
