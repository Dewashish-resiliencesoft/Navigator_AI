"""Deterministic flow trigger matching from site graph semantics."""

from __future__ import annotations


def match_flow_triggers(
    utterance: str,
    *,
    graph,
    page_id: str,
) -> tuple[str, str] | None:
    """Return (flow_id, page_id) on substring trigger hit, else None."""
    text = (utterance or "").strip().lower()
    if len(text) < 2:
        return None

    # Search current page first, then all pages.
    page_ids = [page_id] + [p for p in graph.pages if p != page_id]
    for pid in page_ids:
        page = graph.pages.get(pid)
        if page is None:
            continue
        for fid in page.flows:
            sem = graph.flow_semantics(fid)
            triggers = sem.get("triggers")
            if not isinstance(triggers, list):
                continue
            for raw in triggers:
                trig = str(raw).strip().lower()
                if len(trig) >= 3 and trig in text:
                    return fid, pid
    return None


def flow_trigger_texts(graph, page_id: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    page = graph.pages.get(page_id)
    if page is None:
        return out
    for fid in page.flows:
        sem = graph.flow_semantics(fid)
        triggers = sem.get("triggers")
        if isinstance(triggers, list):
            out[fid] = [str(t).strip() for t in triggers if str(t).strip()]
    return out
