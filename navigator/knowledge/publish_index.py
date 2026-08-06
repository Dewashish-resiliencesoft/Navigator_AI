"""Re-index knowledge and flow intents when a site graph is published."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from navigator.knowledge.memory.collections import get_collection
from navigator.knowledge.product_brief import load_product_brief
from navigator.knowledge.site_graph import SiteGraph


@dataclass(frozen=True)
class PublishIndexResult:
    knowledge_chunks: int
    flow_intent_chunks: int
    revision: int

    def as_dict(self) -> dict:
        return {
            "knowledge_chunks": self.knowledge_chunks,
            "flow_intent_chunks": self.flow_intent_chunks,
            "revision": self.revision,
        }


def _flow_intent_text(graph: SiteGraph, page_id: str, flow_id: str) -> str:
    sem = graph.flow_semantics(flow_id)
    parts = [flow_id.replace("_", " ").replace("-", " ")]
    name = ""
    for item in graph.demo_playlist:
        if item.page_id == page_id and item.flow_id == flow_id and item.name.strip():
            name = item.name.strip()
            break
    if name:
        parts.append(name)
    purpose = str(sem.get("purpose") or "").strip()
    if purpose:
        parts.append(purpose)
    tags = sem.get("tags")
    if isinstance(tags, list):
        parts.extend(str(t).strip() for t in tags if str(t).strip())
    triggers = sem.get("triggers")
    if isinstance(triggers, list):
        parts.extend(str(t).strip() for t in triggers if str(t).strip())
    return " — ".join(parts)


def index_on_publish(
    *,
    product_id: str,
    graph: SiteGraph,
    revision: int,
    chroma_path: str | Path,
) -> PublishIndexResult:
    """Upsert product brief + per-flow intent docs tied to published revision."""
    path = Path(chroma_path)
    k_coll = get_collection(path, product_id, "product_knowledge")
    flow_coll = get_collection(path, product_id, "flow_intents")

    knowledge_chunks = 0
    brief = load_product_brief(product_id).strip()
    if brief:
        doc_id = f"brief-rev-{revision}"
        k_coll.upsert(
            ids=[doc_id],
            documents=[brief],
            metadatas=[
                {
                    "product_id": product_id,
                    "kind": "product_brief",
                    "revision_tied_to": revision,
                    "category": "brief",
                    "summary": "product brief",
                }
            ],
        )
        knowledge_chunks += 1

    flow_intent_chunks = 0
    for page_id, page in graph.pages.items():
        for flow_id in page.flows:
            text = _flow_intent_text(graph, page_id, flow_id)
            if not text.strip():
                continue
            doc_id = f"flow-{page_id}-{flow_id}-rev-{revision}"
            flow_coll.upsert(
                ids=[doc_id],
                documents=[text],
                metadatas=[
                    {
                        "product_id": product_id,
                        "kind": "flow_intent",
                        "page_id": page_id,
                        "flow_id": flow_id,
                        "revision_tied_to": revision,
                    }
                ],
            )
            flow_intent_chunks += 1

            # Also mirror into product_knowledge for unified retrieve_context.
            kid = f"flow-k-{page_id}-{flow_id}-{uuid4().hex[:8]}"
            k_coll.upsert(
                ids=[kid],
                documents=[text],
                metadatas=[
                    {
                        "product_id": product_id,
                        "kind": "flow_intent",
                        "page_id": page_id,
                        "flow_id": flow_id,
                        "revision_tied_to": revision,
                        "category": "flow",
                        "summary": flow_id,
                    }
                ],
            )
            knowledge_chunks += 1

    return PublishIndexResult(
        knowledge_chunks=knowledge_chunks,
        flow_intent_chunks=flow_intent_chunks,
        revision=revision,
    )


def index_knowledge_draft(
    *,
    product_id: str,
    text: str,
    revision: int,
    chroma_path: str | Path,
) -> str:
    """Upsert product brief from dashboard knowledge editor (draft revision)."""
    path = Path(chroma_path)
    coll = get_collection(path, product_id, "product_knowledge")
    doc_id = f"brief-draft-rev-{revision}"
    coll.upsert(
        ids=[doc_id],
        documents=[text.strip()],
        metadatas=[
            {
                "product_id": product_id,
                "kind": "product_brief",
                "revision_tied_to": revision,
                "category": "brief",
                "summary": "product brief",
            }
        ],
    )
    return doc_id
