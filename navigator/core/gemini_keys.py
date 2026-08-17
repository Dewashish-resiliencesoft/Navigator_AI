"""Gemini API key list + quota-error detection for primary/backup failover."""

from __future__ import annotations

from navigator.core.key_pool import is_rate_limit_error, parse_key_list
from navigator.core.settings import settings


def gemini_key_candidates() -> list[str]:
    """Primary, backup, then comma-separated pool; deduped."""
    return parse_key_list(
        settings.gemini_api_key,
        settings.gemini_api_key_backup,
        settings.gemini_api_keys,
    )


def is_gemini_quota_error(exc: BaseException) -> bool:
    return is_rate_limit_error(exc)


def is_gemini_live_unavailable(exc: BaseException) -> bool:
    """True for transient Gemini Live socket failures (retry / rotate key)."""
    msg = str(exc).lower()
    return (
        "1011" in msg
        or "currently unavailable" in msg
        or "overloaded" in msg
        or ("service" in msg and "unavailable" in msg)
    )
