"""Retrieval at planning time.

Two things matter more than the similarity search:

  product_id -- every function takes it, and it is not optional. A correction
    retrieved from the wrong product's collection would be silently injected into
    a live sales call, so this is not a parameter to default.

  metadata filters -- a correction about the inbox page's send button is noise
    when the agent is on a settings page. `page` and `tool_call_type` are applied
    before ranking, not after.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from navigator.memory.collections import get_collection
from navigator.settings import settings


class Correction(BaseModel):
    """An approved corrective rule, as stored in a product's corrections
    collection."""

    model_config = ConfigDict(frozen=True)

    rule: str
    product_id: str
    page: str
    tool_call_type: str
    """One of the ToolCall `tool` literals."""
    source_call_id: str
    """The ActionLog entry this was learned from -- keeps rules traceable back to
    the failure that produced them."""


def retrieve_corrections(
    product_id: str,
    query: str,
    page: str,
    tool_call_type: str | None = None,
    k: int = 5,
    path: str | Path | None = None,
) -> list[Correction]:
    chroma_path = path if path is not None else settings.chroma_path
    coll = get_collection(chroma_path, product_id, "corrections")
    if coll.count() == 0:
        return []

    if tool_call_type is None:
        where: dict = {"page": page}
    else:
        where = {
            "$and": [
                {"page": page},
                {"tool_call_type": tool_call_type},
            ]
        }

    result = coll.query(
        query_texts=[query], n_results=min(k, coll.count()), where=where
    )
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    out: list[Correction] = []
    for doc, meta in zip(docs, metas, strict=True):
        meta = meta or {}
        pid = meta.get("product_id", "")
        if pid != product_id:
            raise AssertionError(
                f"tenant leak: expected product_id={product_id!r}, got {pid!r}"
            )
        out.append(
            Correction(
                rule=doc,
                product_id=pid,
                page=meta["page"],
                tool_call_type=meta["tool_call_type"],
                source_call_id=meta["source_call_id"],
            )
        )
    return out


def retrieve_product_knowledge(
    product_id: str,
    query: str,
    k: int = 5,
    path: str | Path | None = None,
) -> list[str]:
    chroma_path = path if path is not None else settings.chroma_path
    coll = get_collection(chroma_path, product_id, "product_knowledge")
    if coll.count() == 0:
        return []
    result = coll.query(query_texts=[query], n_results=min(k, coll.count()))
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    out: list[str] = []
    for doc, meta in zip(docs, metas, strict=True):
        meta = meta or {}
        if meta.get("product_id") != product_id:
            raise AssertionError(
                f"tenant leak: expected product_id={product_id!r}, "
                f"got {meta.get('product_id')!r}"
            )
        out.append(doc)
    return out
