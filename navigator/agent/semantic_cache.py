"""In-memory semantic cache for prefetch during demo (VoiceAgentRAG-lite)."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock

from navigator.knowledge.context import RetrievalResult


@dataclass
class SemanticCache:
    max_entries: int = 64
    _data: OrderedDict[str, RetrievalResult] = field(default_factory=OrderedDict)
    _lock: Lock = field(default_factory=Lock)

    def _key(self, product_id: str, query: str) -> str:
        return f"{product_id}:{query.strip().lower()}"

    def get(self, product_id: str, query: str) -> RetrievalResult | None:
        key = self._key(product_id, query)
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        return None

    def put(self, product_id: str, query: str, result: RetrievalResult) -> None:
        key = self._key(product_id, query)
        with self._lock:
            self._data[key] = result
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def prefetch_queries(self, base_query: str) -> list[str]:
        """Likely follow-ups from a base utterance — ponytail: keyword heuristic."""
        q = base_query.lower()
        out = [base_query]
        if "pric" in q:
            out.extend(["how much does it cost", "billing", "enterprise plan"])
        if "search" in q or "find" in q:
            out.extend(["show me contact search", "find a contact"])
        if "send" in q or "message" in q:
            out.extend(["how do I send a message", "show messaging"])
        return list(dict.fromkeys(out))
