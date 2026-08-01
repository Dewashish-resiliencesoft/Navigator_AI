"""Upsert helpers for tests and local seeding. Not an HTTP API."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from navigator.knowledge.memory.collections import get_collection


def seed_correction(
    path: str | Path,
    *,
    product_id: str,
    rule: str,
    page: str,
    tool_call_type: str,
    source_call_id: str,
    doc_id: str | None = None,
) -> str:
    coll = get_collection(path, product_id, "corrections")
    doc_id = doc_id or str(uuid4())
    coll.upsert(
        ids=[doc_id],
        documents=[rule],
        metadatas=[
            {
                "product_id": product_id,
                "page": page,
                "tool_call_type": tool_call_type,
                "source_call_id": source_call_id,
            }
        ],
    )
    return doc_id


def seed_knowledge(
    path: str | Path,
    *,
    product_id: str,
    text: str,
    doc_id: str | None = None,
) -> str:
    coll = get_collection(path, product_id, "product_knowledge")
    doc_id = doc_id or str(uuid4())
    coll.upsert(
        ids=[doc_id],
        documents=[text],
        metadatas=[{"product_id": product_id}],
    )
    return doc_id
