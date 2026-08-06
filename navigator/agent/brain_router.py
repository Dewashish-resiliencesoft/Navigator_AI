"""High-level brain router — wraps intent + triggers + retrieval cache."""

from __future__ import annotations

from navigator.agent.brain_decision import BrainDecision
from navigator.agent.intent_router import route_flow_from_triggers, route_intent
from navigator.agent.semantic_cache import SemanticCache
from navigator.knowledge.context import RetrievalResult, retrieve_context

# Module-level cache per demo process (ponytail: one LRU, keyed by product+query).
_CACHE = SemanticCache()


def get_semantic_cache() -> SemanticCache:
    return _CACHE


def route_turn(
    *,
    utterance: str,
    phase: str,
    graph,
    page_id: str,
    product_id: str,
    flow_texts: dict[str, str] | None = None,
    chroma_path=None,
    retrieve=retrieve_context,
) -> BrainDecision:
    """First pass routing before planning waterfall."""
    ruled = route_intent(utterance=utterance, phase=phase)
    if ruled.intent != "unknown":
        return ruled

    trigger = route_flow_from_triggers(utterance, graph=graph, page_id=page_id)
    if trigger is not None:
        return trigger

    cached = _CACHE.get(product_id, utterance)
    if cached is not None and cached.candidate_flows:
        flow_id, conf = cached.candidate_flows[0]
        return BrainDecision(
            intent="run_flow",
            flow_id=flow_id,
            confidence=conf,
            branch="flow_executed",
            detail="semantic cache hit",
            router="cache",
        )

    result = retrieve(
        utterance,
        product_id,
        flow_texts=flow_texts,
        available_flow_ids=list((flow_texts or {}).keys()),
        chroma_path=chroma_path,
    )
    _CACHE.put(product_id, utterance, result)
    return _brain_from_retrieval(result)


def prefetch_context(
    *,
    product_id: str,
    base_query: str,
    flow_texts: dict[str, str] | None,
    chroma_path=None,
    retrieve=retrieve_context,
) -> None:
    for q in _CACHE.prefetch_queries(base_query):
        if _CACHE.get(product_id, q) is not None:
            continue
        try:
            result = retrieve(
                q,
                product_id,
                flow_texts=flow_texts,
                available_flow_ids=list((flow_texts or {}).keys()),
                chroma_path=chroma_path,
            )
            _CACHE.put(product_id, q, result)
        except Exception:  # noqa: BLE001
            continue


def _brain_from_retrieval(result: RetrievalResult) -> BrainDecision:
    band = result.flow_band()
    if band in {"high", "medium"} and result.best_flow:
        fid, conf = result.best_flow
        return BrainDecision(
            intent="run_flow",
            flow_id=fid,
            confidence=conf,
            branch="flow_executed",
            detail=f"retrieve band={band}",
            router="retrieve",
        )
    if result.relevant_knowledge:
        return BrainDecision(
            intent="answer",
            confidence=result.relevant_knowledge[0][1],
            branch="knowledge_only",
            detail="knowledge hit",
            router="retrieve",
        )
    return BrainDecision(
        intent="handoff",
        branch="handoff",
        detail="retrieve miss",
        router="retrieve",
    )
