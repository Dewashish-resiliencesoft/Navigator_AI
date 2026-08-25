"""Lightweight hybrid retrieval helpers (sparse token overlap + RRF)."""

from __future__ import annotations

import math
import re
from collections import Counter


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def sparse_score(query: str, document: str) -> float:
    """BM25-ish lite without extra deps — enough for keyword/trigger overlap."""
    q_tokens = tokenize(query)
    if not q_tokens:
        return 0.0
    d_tokens = tokenize(document)
    if not d_tokens:
        return 0.0
    df = Counter(d_tokens)
    score = 0.0
    for tok in set(q_tokens):
        tf = df.get(tok, 0)
        if tf:
            score += 1.0 + math.log(1.0 + tf)
    return score / len(set(q_tokens))


def reciprocal_rank_fusion(
    *ranked_lists: list[tuple[str, float]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Merge ranked id lists by RRF score."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, (item_id, _raw) in enumerate(ranked):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])
