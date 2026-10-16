"""REFLECTING: failures used to draft pending correction rules.

Pending-correction review was removed. This node is a no-op so the graph
still has a reflecting phase without writing review queue rows.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from navigator.agent.state import CallDeps, CallState
from navigator.core.schemas import ActionLogEntry
from navigator.core.settings import settings

CLASSIFY_MODEL = settings.brain_classify_model

_NOT_CORRECTION = re.compile(
    r"\b(take me to|show me|go to|open the|navigate|404|not found|"
    r"wrong page|end the meeting|goodbye|what is this)\b",
    re.I,
)

#: A correction always contests what the agent just did. Utterances with no such
#: cue skip the classifier LLM entirely — that call sits in the turn's critical
#: path, and the overwhelming majority of utterances are not corrections.
_MAYBE_CORRECTION = re.compile(
    r"\b(no|not|wrong|isn'?t|didn'?t|don'?t|stop|wait|back|undo|mistake|"
    r"should(n'?t)?|meant|instead|actually|other one|that'?s not)\b",
    re.I,
)


def reflecting(state: CallState, deps: CallDeps) -> CallState:
    failures = list(state.get("failures") or [])
    if failures:
        print(
            f"[reflect] skipped {len(failures)} failure(s) "
            "(pending corrections removed)",
            flush=True,
        )
    return CallState()


def classify_correction(
    utterance: str,
    last_action: ActionLogEntry | None,
    *,
    api_key: str | None = None,
    complete: Callable[[str], str] | None = None,
) -> bool:
    """Cheap yes/no: is this utterance correcting the agent's last action?"""
    if not utterance.strip():
        return False
    # Nav / UI complaints / end — never corrections (even if LLM says yes).
    if _NOT_CORRECTION.search(utterance):
        return False
    if not _MAYBE_CORRECTION.search(utterance):
        return False
    prompt = (
        "Answer ONLY yes or no. Is the user correcting HOW the agent demoed "
        "(wrong click, wrong field, wrong step)? "
        "Navigation requests ('take me to X', 'show me Y') and UI bugs "
        "(404, wrong page) are NOT corrections — answer no.\n"
        f"Utterance: {utterance}\n"
        f"Last action: {None if last_action is None else last_action.tool_call.tool}"
    )
    if complete is not None:
        raw = complete(prompt)
    else:
        key = api_key if api_key is not None else settings.groq_api_key
        if not key:
            return False
        from navigator.core.groq_client import chat_completions_create

        resp = chat_completions_create(
            key,
            purpose="reflect_classify",
            model=CLASSIFY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=3,
        )
        raw = (resp.choices[0].message.content or "").strip()
    return raw.lower().startswith("y")
