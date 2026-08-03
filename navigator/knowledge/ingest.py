"""Improved ingestion: semantic chunking, LLM tagging, dedup."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from navigator.knowledge.memory.collections import get_collection
from navigator.core.settings import settings


def _chunk_text(text: str, target_tokens: int = 350) -> list[str]:
    """Semantic chunking by paragraphs, respecting sentence boundaries.

    Targets ~target_tokens per chunk (rough; no actual tokenization).
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current_chunk: list[str] = []
    current_length = 0
    words_per_token = 1.3  # rough heuristic

    for para in paragraphs:
        para_tokens = len(para.split()) / words_per_token
        # Start a new chunk if adding this para would exceed target AND we have content
        if current_chunk and current_length + para_tokens > target_tokens:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [para]
            current_length = para_tokens
        else:
            current_chunk.append(para)
            current_length += para_tokens

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))
    return chunks


def _categorize_chunk(
    text: str,
    judge: Callable[[str], str] | None = None,
) -> tuple[str, str]:
    """LLM-assisted tagging: category + summary.

    Returns (category, summary). Both are short strings.
    If judge is None, returns ("general", "").
    """
    if judge is None:
        return "general", ""

    prompt = f"""Classify this product documentation chunk. Reply with JSON only.

Chunk:
{text[:500]}

Reply: {{"category": "<one word: Billing|Contacts|Reporting|Settings|etc>", "summary": "<one sentence>"}}"""

    try:
        import json
        import re

        raw = judge(prompt)
        match = re.search(r"\{.*\}", raw or "", re.S)
        if not match:
            return "general", ""
        data = json.loads(match.group(0))
        cat = str(data.get("category", "")).strip() or "general"
        summ = str(data.get("summary", "")).strip()
        return cat, summ
    except Exception:  # noqa: BLE001
        return "general", ""


def ingest_knowledge_text(
    text: str,
    product_id: str,
    revision_tied_to: int | None = None,
    judge: Callable[[str], str] | None = None,
    chroma_path: str | Path | None = None,
) -> list[str]:
    """Chunk, tag, and deduplicated ingest.

    Returns list of chunk IDs (either new or existing from dedup).
    """
    chroma_path = chroma_path or settings.chroma_path
    coll = get_collection(chroma_path, product_id, "product_knowledge")

    chunks = _chunk_text(text)
    result_ids: list[str] = []

    for chunk_text in chunks:
        # Dedup by hash
        chunk_id = hashlib.sha256(chunk_text.encode()).hexdigest()[:16]
        category, summary = _categorize_chunk(chunk_text, judge)

        # Upsert: if chunk_id exists, this is a no-op (same text). If new, it's added.
        coll.upsert(
            ids=[chunk_id],
            documents=[chunk_text],
            metadatas=[
                {
                    "product_id": product_id,
                    "chunk_id": chunk_id,
                    "category": category,
                    "summary": summary,
                    "revision_tied_to": revision_tied_to,
                    "ingested_at": __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ).isoformat(),
                }
            ],
        )
        result_ids.append(chunk_id)

    return result_ids
