"""REFLECTING: turn failures and user corrections into corrective rules.

STUB. Phase 1 records failures in state and in the ActionLog but derives nothing
from them -- which is the point of logging them structurally now.

Two paths when this is filled in:
  - in-call: apply a detected correction as short-term context immediately, no
    round trip to a slow model
  - post-call: batch this session's failures, one reflection call per failure,
    write results to a PENDING review table. Never auto-promote into the live
    corrections collection; a human approves first.
"""

from __future__ import annotations

from navigator.agent.state import CallDeps, CallState
from navigator.schemas import ActionLogEntry


def reflecting(state: CallState, deps: CallDeps) -> CallState:
    # TODO(phase 4): for each entry in state["failures"], call the configured
    # LLMProvider (navigator.agent.providers -- Gemini 2.5 Flash free, or
    # gpt-4o-mini paid) with {page, tool_call, expected_postcondition,
    # actual_result} and ask for one short corrective rule. Write to the pending
    # review table, tagged with page + tool_call_type metadata so Phase 2's
    # retrieval can filter on them once approved.
    return CallState()


def classify_correction(utterance: str, last_action: ActionLogEntry | None) -> bool:
    """Is this utterance the user correcting the agent's last action?

    STUB. Cheap Groq classifier, run on every user utterance -- so it must stay
    one small fast call, not a reasoning step.
    """
    # TODO(phase 4): Groq llama-3.1-8b-instant, single yes/no. 8b not 70b: this
    # runs per utterance and the 70b free tier is capped at 1000 requests/day.
    raise NotImplementedError("correction classifier lands in Phase 4")
