"""REFLECTING: turn failures into corrective rules (pending human review)."""

from __future__ import annotations

import re
from collections.abc import Callable

from navigator.agent.providers import LLMProvider, get_provider
from navigator.agent.state import CallDeps, CallState
from navigator.knowledge.memory.pending import PendingCorrectionStore
from navigator.core.schemas import ActionLogEntry
from navigator.core.settings import settings

REFLECT_SYSTEM = (
    "You write one short corrective rule for a demo agent that drives a web app "
    "via a site graph. Rule must be actionable and specific. No selectors unless "
    "they are aliases already mentioned. Return ONLY the rule text."
)

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
    if not failures:
        return CallState()

    provider = deps.reflect_provider
    if provider is None:
        try:
            provider = get_provider()
        except RuntimeError as exc:
            print(f"[reflect] skipped (no provider): {exc}", flush=True)
            return CallState()

    store_path = deps.pending_db_path or settings.db_path
    store = PendingCorrectionStore(store_path)
    try:
        for entry in failures:
            rule = _reflect_one(provider, entry)
            if not rule:
                continue
            store.add(
                product_id=deps.product_id,
                session_id=state["session_id"],
                page=entry.page,
                tool_call_type=entry.tool_call.tool,
                rule=rule,
                source_call_id=entry.call_id,
            )
            print(f"[reflect] pending rule: {rule!r}", flush=True)
    finally:
        store.close()
    return CallState()


def _reflect_one(provider: LLMProvider, entry: ActionLogEntry) -> str:
    user = (
        f"page={entry.page}\n"
        f"tool={entry.tool_call.tool}\n"
        f"expected={entry.expected_postcondition.model_dump()}\n"
        f"actual={entry.actual_result.model_dump()}\n"
        f"verify={None if entry.verify is None else entry.verify.model_dump()}\n"
        "Write one corrective rule."
    )
    try:
        return provider.complete(REFLECT_SYSTEM, user).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[reflect] provider failed: {exc}", flush=True)
        return ""


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
