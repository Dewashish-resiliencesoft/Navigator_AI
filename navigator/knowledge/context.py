"""Unified retrieval interface: knowledge chunks, candidate flows, product areas.

Single entry point for all context-gathering at planning time and later at
answer time. Returns knowledge freshness status tied to site graph revisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from navigator.app.registry import Registry, ProductNotFound
from navigator.knowledge.memory.collections import get_collection
from navigator.core.settings import settings


@dataclass(frozen=True)
class KnowledgeChunk:
    """A semantic piece of product knowledge."""

    id: str
    product_id: str
    text: str
    category: str
    summary: str
    revision_tied_to: int | None
    created_at: str


@dataclass(frozen=True)
class ProductMapArea:
    """Top-level capability area bridging site graph and knowledge."""

    product_id: str
    area_id: str
    name: str
    purpose: str
    related_flow_ids: list[str]
    related_chunk_ids: list[str]
    categories: set[str]


@dataclass(frozen=True)
class RetrievalResult:
    """All context for one query."""

    product_id: str
    query: str
    knowledge_chunks: list[tuple[KnowledgeChunk, float]]
    """Chunks ranked by similarity. Score ∈ [0, 1]."""
    candidate_flows: list[tuple[str, float]]
    """(flow_id, confidence). Confidence is a heuristic, not a model score."""
    relevant_areas: list[tuple[ProductMapArea, float]]
    """(area, relevance). Not populated in v1; reserved for future synthesis."""
    knowledge_based_on_revision: int | None
    """Active revision when knowledge was ingested. None if unknown."""
    current_published_revision: int | None
    """Latest published revision. None if no revision is published."""
    is_stale: bool
    """True if knowledge was ingested when a different revision was active."""

    @property
    def has_knowledge(self) -> bool:
        return len(self.knowledge_chunks) > 0

    @property
    def has_flows(self) -> bool:
        return len(self.candidate_flows) > 0


def retrieve_context(
    query: str,
    product_id: str,
    *,
    available_flow_ids: list[str] | None = None,
    registry: Registry | None = None,
    k_knowledge: int = 5,
    k_flows: int = 3,
    chroma_path: str | Path | None = None,
) -> RetrievalResult:
    """Unified retrieval: knowledge + flows + staleness.

    All context for planning or answer nodes, in one call.
    """
    chroma_path = chroma_path or settings.chroma_path
    available_flow_ids = available_flow_ids or []

    # Retrieve knowledge chunks + extract revision info from metadata
    coll = get_collection(chroma_path, product_id, "product_knowledge")
    knowledge_chunks: list[tuple[KnowledgeChunk, float]] = []
    revision_tied_to: int | None = None

    if coll.count() > 0:
        result = coll.query(query_texts=[query], n_results=min(k_knowledge, coll.count()))
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        for doc, meta, distance in zip(docs, metas, distances, strict=False):
            meta = meta or {}
            if meta.get("product_id") != product_id:
                continue  # skip cross-tenant leak
            # Chroma distances are Euclidean; convert to similarity ∈ [0, 1]
            similarity = max(0.0, 1.0 - distance)
            chunk = KnowledgeChunk(
                id=meta.get("chunk_id", ""),
                product_id=product_id,
                text=doc,
                category=meta.get("category", ""),
                summary=meta.get("summary", ""),
                revision_tied_to=_parse_int(meta.get("revision_tied_to")),
                created_at=meta.get("ingested_at", ""),
            )
            knowledge_chunks.append((chunk, similarity))
            # Track the most recent revision knowledge was tied to
            if chunk.revision_tied_to is not None:
                if revision_tied_to is None or chunk.revision_tied_to > revision_tied_to:
                    revision_tied_to = chunk.revision_tied_to

    # Get current published revision to check staleness
    current_revision: int | None = None
    if registry is not None:
        try:
            current_revision = registry.published_revision(product_id)
        except ProductNotFound:
            current_revision = None

    # Check staleness: knowledge was ingested when a different revision was active
    is_stale = (
        revision_tied_to is not None
        and current_revision is not None
        and revision_tied_to != current_revision
    )

    # Flow candidates (v1: just return the ones available, no scoring)
    candidate_flows: list[tuple[str, float]] = [
        (flow_id, 1.0) for flow_id in available_flow_ids
    ]

    return RetrievalResult(
        product_id=product_id,
        query=query,
        knowledge_chunks=knowledge_chunks,
        candidate_flows=candidate_flows,
        relevant_areas=[],
        knowledge_based_on_revision=revision_tied_to,
        current_published_revision=current_revision,
        is_stale=is_stale,
    )


def _parse_int(v: object) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            return None
    return None
