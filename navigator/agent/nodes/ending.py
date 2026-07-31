"""ENDING: archive the transcript and the structured action log."""

from __future__ import annotations

import json

from navigator.agent.state import CallDeps, CallState


def ending(state: CallState, deps: CallDeps) -> CallState:
    session_id = state["session_id"]
    # Namespaced per product so one customer's transcripts never land in another's
    # directory, and so a deployment can mount one volume per tenant.
    out = deps.archive_dir / deps.product_id
    out.mkdir(parents=True, exist_ok=True)

    (out / f"{session_id}-transcript.txt").write_text(
        "\n".join(state.get("transcript", []))
    )

    # Read back from the log rather than from state: the DB is the source of truth,
    # and the round trip proves the rows are actually retrievable.
    entries = deps.log.entries(session_id, product_id=deps.product_id)
    (out / f"{session_id}-actions.json").write_text(
        json.dumps([json.loads(e.model_dump_json()) for e in entries], indent=2)
    )

    failures = [e for e in entries if e.failed]
    summary = (
        f"Archived {len(entries)} action(s), {len(failures)} failure(s), "
        f"to {out}/{session_id}-*"
    )
    print(f"[end] {summary}")
    return CallState(finished=True, transcript=[f"[{summary}]"])
