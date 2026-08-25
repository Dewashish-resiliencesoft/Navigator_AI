"""Stable utterance IDs for the narration queue.

One logical response (walkthrough step, planner turn, intro) keeps the same
id even when the spoken text is regenerated in another language. Duplicate
ids are dropped at enqueue; SPEAKING skips ids already consumed.
"""

from __future__ import annotations

from typing import Any, Iterable


def logic_id(state: dict[str, Any], *, kind: str, index: int = 0) -> str:
    return (
        f"{kind}:"
        f"{state.get('walkthrough_flow_id') or ''}:"
        f"{int(state.get('walkthrough_step') or 0)}:"
        f"{int(state.get('detour_step') or 0)}:"
        f"{int(state.get('turns') or 0)}:"
        f"{index}"
    )


def item_text(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("text") or "")
    return "" if raw is None else str(raw)


def item_id(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("id") or "")
    return ""


def stamp_narration(
    state: dict[str, Any], lines: Iterable[Any], *, kind: str
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for i, raw in enumerate(lines or []):
        if isinstance(raw, dict) and raw.get("text") is not None:
            uid = str(raw.get("id") or logic_id(state, kind=kind, index=i))
            items.append({"id": uid, "text": str(raw.get("text") or "")})
            continue
        text = item_text(raw)
        if not text.strip():
            continue
        items.append({"id": logic_id(state, kind=kind, index=i), "text": text})
    return items


def merge_narration(existing: list, new: list) -> list:
    out: list = []
    seen: set[str] = set()
    for raw in list(existing or []) + list(new or []):
        if isinstance(raw, dict) and raw.get("id"):
            uid = str(raw["id"])
            if uid in seen:
                print(
                    f"[utterance] drop duplicate id={uid} queue={len(out)}",
                    flush=True,
                )
                continue
            seen.add(uid)
            out.append(raw)
            continue
        out.append(raw)
    return out
