"""Explore episode learning — pending-correction drafting removed.

Kept as a no-op so explore runner call sites stay stable until explore is
deleted entirely.
"""

from __future__ import annotations

from typing import Any, Callable

from navigator.automation.explore.episode import EpisodeStore


def draft_rules(
    episode: EpisodeStore,
    *,
    product_id: str,
    session_id: str,
    pending_db_path: str | Any,
    ask_text: Callable[[str], str] | None = None,
    complete: Callable[[str, str], str] | None = None,
) -> list[str]:
    """Formerly drafted PendingCorrectionStore rules. Always returns []."""
    del episode, product_id, session_id, pending_db_path, ask_text, complete
    return []
