"""Unified retrieval interface: knowledge chunks, candidate flows, product areas.

Single entry point for all context-gathering at planning time and later at
answer time. Returns knowledge freshness status tied to site graph revisions.

Both score scales here are cosine similarity in [0, 1], so the thresholds below
mean the same thing for a flow match and a knowledge hit. That is the whole
reason the live decision step can branch on one number.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from navigator.app.registry import Registry, ProductNotFound
from navigator.knowledge.memory.collections import get_collection
from navigator.knowledge.hybrid_retrieval import reciprocal_rank_fusion, sparse_score
from navigator.core.settings import settings

#: Run the matched flow without asking. Measured: a direct request for a flow
#: ("how do I message someone" vs `send_message`) scores 0.64-0.76.
HIGH_CONFIDENCE = 0.55
#: Ask one clarifying question first. Below this, treat as no flow match at all:
#: an unrelated utterance scores under 0.10, a vague one 0.08-0.31.
MEDIUM_CONFIDENCE = 0.30
#: A knowledge chunk worth answering from. Irrelevant text lands at ~0.0.
KNOWLEDGE_RELEVANT = 0.35


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


def _similarity(distance: float, space: str) -> float:
    """Chroma distance -> cosine similarity in [0, 1].

    Handles both spaces because collections created before this module existed
    are `l2` on disk, and Chroma silently ignores a space override on an existing
    collection -- so the scale has to be read, not assumed.

    For unit-norm embeddings Chroma's `l2` is the *squared* euclidean distance,
    which equals 2 - 2*cos. Both branches therefore yield the same number, and a
    threshold tuned on one space holds on the other with no reindexing.
    """
    d = max(0.0, float(distance))
    sim = 1.0 - (d / 2.0) if space == "l2" else 1.0 - d
    return min(1.0, max(0.0, sim))


def _collection_space(coll) -> str:
    """The distance metric a collection actually uses on disk."""
    try:
        hnsw = (coll.configuration_json or {}).get("hnsw") or {}
        return str(hnsw.get("space") or "l2").lower()
    except Exception:  # noqa: BLE001
        return "l2"


def flow_text(flow_id: str, *, name: str = "", trigger_intent: str = "") -> str:
    """What a flow is matched against.

    A flow id is already meaningful English in a well-authored site graph
    ("send_message"), which is what makes matching work with no extra authoring.
    A playlist name or an explicit `trigger_intent` sharpens it when present.
    """
    parts = [flow_id.replace("_", " ").replace("-", " ")]
    if name.strip() and name.strip().lower() != parts[0]:
        parts.append(name.strip())
    if trigger_intent.strip():
        parts.append(trigger_intent.strip())
    return " — ".join(parts)


@lru_cache(maxsize=8)
def _embedder():
    """Chroma's default embedder, reused across turns.

    Cached because a live turn is latency-sensitive and constructing this loads a
    model; scoring a handful of short strings after that is microseconds.
    """
    from chromadb.utils import embedding_functions

    return embedding_functions.DefaultEmbeddingFunction()


def score_flows(
    query: str,
    flow_texts: dict[str, str],
    *,
    embedder=None,
) -> list[tuple[str, float]]:
    """Rank flows against an utterance by cosine similarity, best first.

    Embeds rather than keyword-matches so "how do I message someone" finds
    `send_message` without either string containing the other.
    """
    if not query.strip() or not flow_texts:
        return []
    embed = embedder if embedder is not None else _embedder()
    flow_ids = list(flow_texts)
    try:
        vectors = embed([query, *(flow_texts[f] for f in flow_ids)])
    except Exception as exc:  # noqa: BLE001
        print(f"[retrieve] flow scoring unavailable ({exc}); no candidates", flush=True)
        return []

    query_vec, flow_vecs = vectors[0], vectors[1:]
    dense = sorted(
        [
            (flow_id, _cosine(query_vec, vec))
            for flow_id, vec in zip(flow_ids, flow_vecs, strict=False)
        ],
        key=lambda pair: -pair[1],
    )
    sparse = sorted(
        [(fid, sparse_score(query, flow_texts[fid])) for fid in flow_ids],
        key=lambda pair: -pair[1],
    )
    # Trigger substring boost — score 1.0 when utterance contains a listed trigger.
    trigger_hits: list[tuple[str, float]] = []
    q_low = query.lower()
    for fid, text in flow_texts.items():
        for part in text.split(" — "):
            p = part.strip().lower()
            if len(p) >= 3 and p in q_low:
                trigger_hits.append((fid, 1.0))
                break
    lists = [dense, sparse]
    if trigger_hits:
        lists.append(sorted(trigger_hits, key=lambda x: -x[1]))
    fused = reciprocal_rank_fusion(*lists)
    return [(fid, score) for fid, score in fused]


def _cosine(a, b) -> float:
    dot = sum(float(x) * float(y) for x, y in zip(a, b, strict=False))
    na = sum(float(x) * float(x) for x in a) ** 0.5
    nb = sum(float(y) * float(y) for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    # Negative similarity is real but meaningless for ranking a match.
    return min(1.0, max(0.0, dot / (na * nb)))


@dataclass(frozen=True)
class RetrievalResult:
    """All context for one query."""

    product_id: str
    query: str
    knowledge_chunks: list[tuple[KnowledgeChunk, float]]
    """Chunks ranked by similarity. Score ∈ [0, 1]."""
    candidate_flows: list[tuple[str, float]]
    """(flow_id, cosine similarity to the query), best first."""
    relevant_areas: list[tuple[ProductMapArea, float]]
    """(area, relevance). Populated from ProductMapStore when areas exist."""
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

    @property
    def best_flow(self) -> tuple[str, float] | None:
        return self.candidate_flows[0] if self.candidate_flows else None

    @property
    def best_knowledge(self) -> tuple[KnowledgeChunk, float] | None:
        return self.knowledge_chunks[0] if self.knowledge_chunks else None

    @property
    def relevant_knowledge(self) -> list[tuple[KnowledgeChunk, float]]:
        """Chunks worth answering from, rather than merely the nearest ones.

        A vector search always returns its k nearest neighbours, so "the top hit"
        is not the same question as "is anything actually relevant".
        """
        return [
            (chunk, score)
            for chunk, score in self.knowledge_chunks
            if score >= KNOWLEDGE_RELEVANT
        ]

    def flow_band(self) -> str:
        """Which confidence band the best flow match falls in: high|medium|none."""
        best = self.best_flow
        if best is None or best[1] < MEDIUM_CONFIDENCE:
            return "none"
        return "high" if best[1] >= HIGH_CONFIDENCE else "medium"


def retrieve_context(
    query: str,
    product_id: str,
    *,
    available_flow_ids: list[str] | None = None,
    flow_texts: dict[str, str] | None = None,
    registry: Registry | None = None,
    k_knowledge: int = 5,
    k_flows: int = 3,
    chroma_path: str | Path | None = None,
    embedder=None,
) -> RetrievalResult:
    """Unified retrieval: knowledge + flows + staleness.

    All context for planning or answer nodes, in one call.

    `flow_texts` maps flow_id -> the text it is matched against (see `flow_text`);
    `available_flow_ids` is the shorthand for when the id is all there is.
    """
    chroma_path = chroma_path or settings.chroma_path
    available_flow_ids = available_flow_ids or []
    if flow_texts is None:
        flow_texts = {f: flow_text(f) for f in available_flow_ids}

    # Retrieve knowledge chunks + extract revision info from metadata
    coll = get_collection(chroma_path, product_id, "product_knowledge")
    knowledge_chunks: list[tuple[KnowledgeChunk, float]] = []
    revision_tied_to: int | None = None

    if query.strip() and coll.count() > 0:
        result = coll.query(query_texts=[query], n_results=min(k_knowledge, coll.count()))
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        space = _collection_space(coll)

        for doc, meta, distance in zip(docs, metas, distances, strict=False):
            meta = meta or {}
            if meta.get("product_id") != product_id:
                continue  # skip cross-tenant leak
            similarity = _similarity(distance, space)
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

        if knowledge_chunks:
            dense = sorted(knowledge_chunks, key=lambda pair: -pair[1])
            sparse = sorted(
                [
                    (pair, sparse_score(query, pair[0].text or ""))
                    for pair in knowledge_chunks
                ],
                key=lambda pair: -pair[1],
            )
            sparse_ids = [
                (chunk.id or chunk.summary or "chunk", score)
                for (chunk, _), score in sparse
            ]
            dense_ids = [
                (chunk.id or chunk.summary or "chunk", score)
                for chunk, score in dense
            ]
            fused_ids = {
                item_id: score for item_id, score in reciprocal_rank_fusion(dense_ids, sparse_ids)
            }
            by_id = {chunk.id or chunk.summary or "chunk": chunk for chunk, _ in knowledge_chunks}
            knowledge_chunks = [
                (by_id[item_id], fused_ids[item_id])
                for item_id in sorted(fused_ids, key=lambda k: -fused_ids[k])
                if item_id in by_id
            ]
            # Track the most recent revision knowledge was tied to
            for chunk, _ in knowledge_chunks:
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

    candidate_flows = score_flows(query, flow_texts, embedder=embedder)[:k_flows]

    relevant_areas: list[tuple[ProductMapArea, float]] = []
    if registry is not None and query.strip():
        try:
            from navigator.knowledge.product_map import ProductMapStore

            areas = ProductMapStore(registry._conn).list_product(product_id)
            if areas:
                area_texts = {
                    a.area_id: f"{a.name} — {a.purpose} — {' '.join(sorted(a.categories))}"
                    for a in areas
                }
                ranked = score_flows(query, area_texts, embedder=embedder)
                by_id = {a.area_id: a for a in areas}
                for area_id, score in ranked[:3]:
                    area = by_id.get(area_id)
                    if area is not None and score >= MEDIUM_CONFIDENCE:
                        relevant_areas.append((area, score))
        except Exception as exc:  # noqa: BLE001
            print(f"[retrieve] product map unavailable ({exc})", flush=True)

    return RetrievalResult(
        product_id=product_id,
        query=query,
        knowledge_chunks=knowledge_chunks,
        candidate_flows=candidate_flows,
        relevant_areas=relevant_areas,
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
