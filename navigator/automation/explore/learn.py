"""Draft pending correction rules from an explore episode. Never writes Chroma."""

from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from navigator.automation.explore.episode import EpisodeStore, StepAttempt
from navigator.knowledge.memory.pending import PendingCorrectionStore

EXPLORE_REFLECT_SYSTEM = (
    "You write one short corrective rule for an autonomous web explorer that "
    "builds demo flows. Rule must be actionable and specific to the failure or "
    "successful repair described. No raw CSS unless an alias is given. "
    "Return ONLY the rule text."
)


def draft_rules(
    episode: EpisodeStore,
    *,
    product_id: str,
    session_id: str,
    pending_db_path: str | Any,
    ask_text: Callable[[str], str] | None = None,
    complete: Callable[[str, str], str] | None = None,
) -> list[str]:
    """LLM-draft rules from unrepaired failures + successful repairs.

    Fail-soft: provider errors print and return whatever was drafted so far.
    Never writes to Chroma — only PendingCorrectionStore.
    """
    sequences = episode.successful_repairs() + episode.unrepaired_failures()
    if not sequences:
        return []

    drafted: list[str] = []
    store = PendingCorrectionStore(pending_db_path)
    try:
        for seq in sequences:
            rule = _draft_one(seq, ask_text=ask_text, complete=complete)
            if not rule:
                continue
            last = seq[-1]
            store.add(
                product_id=product_id,
                session_id=session_id,
                page="main",
                tool_call_type=last.tool,
                rule=rule,
                source_call_id=f"explore:{episode.job_id}:{last.element_key}:{uuid4().hex[:8]}",
            )
            drafted.append(rule)
            print(f"[explore.learn] pending rule: {rule!r}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[explore.learn] draft failed: {exc}", flush=True)
    finally:
        store.close()
    return drafted


def _draft_one(
    seq: list[StepAttempt],
    *,
    ask_text: Callable[[str], str] | None,
    complete: Callable[[str, str], str] | None,
) -> str:
    last = seq[-1]
    repairs = [a for a in seq if a.attempt > 0]
    if last.ok and repairs:
        user = (
            f"A repair succeeded during exploration.\n"
            f"element={last.element_key}\n"
            f"tool={last.tool}\n"
            f"alias={last.alias}\n"
            f"url={last.url_before}\n"
            f"original_kind={seq[0].kind or 'unknown'}\n"
            f"tactics={[a.tactic for a in repairs]}\n"
            f"detail={last.detail}\n"
            "Write one corrective rule so the next explore run avoids the failure."
        )
    else:
        user = (
            f"An exploration step failed and repairs did not recover.\n"
            f"element={last.element_key}\n"
            f"tool={last.tool}\n"
            f"alias={last.alias}\n"
            f"url={last.url_before}\n"
            f"kind={last.kind or 'unknown'}\n"
            f"attempts={len(seq)}\n"
            f"tactics={[a.tactic for a in seq if a.tactic]}\n"
            f"detail={last.detail}\n"
            "Write one corrective rule."
        )

    try:
        if complete is not None:
            return complete(EXPLORE_REFLECT_SYSTEM, user).strip()
        if ask_text is not None:
            # ask_text in explore is a single-prompt helper; fold system in.
            return ask_text(f"{EXPLORE_REFLECT_SYSTEM}\n\n{user}").strip()
        from navigator.agent.providers import get_provider

        return get_provider().complete(EXPLORE_REFLECT_SYSTEM, user).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[explore.learn] provider failed: {exc}", flush=True)
        return ""
