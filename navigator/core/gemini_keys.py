"""Gemini API key list + quota-error detection for primary/backup failover."""

from __future__ import annotations

from navigator.core.key_pool import is_rate_limit_error, parse_key_list
from navigator.core.settings import settings

# Google shut down these ids (404 NOT_FOUND). Saved agent_settings / env pins
# and stale hardcodes still send them — remap at the call boundary.
_RETIRED_GEMINI_MODELS: dict[str, str] = {
    "gemini-2.0-flash": "gemini-3.6-flash",
    "gemini-2.0-flash-001": "gemini-3.6-flash",
    "gemini-2.0-flash-lite": "gemini-3.6-flash",
    "gemini-2.0-flash-lite-001": "gemini-3.6-flash",
}

# models.list still returns shut-down families with no status field — drop by id.
_SHUTDOWN_GEMINI_PREFIXES: tuple[str, ...] = (
    "gemini-1.0",
    "gemini-1.5",
    "gemini-2.0",
)

_DEPRECATION_MARKERS: tuple[str, ...] = (
    "deprecated",
    "no longer available",
    "has been shut down",
    "is shut down",
    "is no longer available",
    "retired",
)


def gemini_model_bare_id(model_id: str) -> str:
    raw = (model_id or "").strip()
    if raw.startswith("models/"):
        return raw.split("/", 1)[1]
    return raw


def is_gemini_model_served(model_id: str) -> bool:
    """False for shut-down Gemini ids Google may still return from models.list."""
    bare = gemini_model_bare_id(model_id)
    if not bare:
        return False
    if bare in _RETIRED_GEMINI_MODELS:
        return False
    return not any(bare == p or bare.startswith(f"{p}-") for p in _SHUTDOWN_GEMINI_PREFIXES)


def gemini_description_deprecated(description: str | None) -> bool:
    low = (description or "").lower()
    return any(marker in low for marker in _DEPRECATION_MARKERS)


def normalize_gemini_model(model_id: str) -> str:
    """Strip models/ prefix; map retired Flash ids to current default."""
    bare = gemini_model_bare_id(model_id)
    if not bare:
        return bare
    return _RETIRED_GEMINI_MODELS.get(bare, bare)


def gemini_key_candidates() -> list[str]:
    """Primary, backup, then comma-separated pool; deduped."""
    return parse_key_list(
        settings.gemini_api_key,
        settings.gemini_api_key_backup,
        settings.gemini_api_keys,
    )


def gemini_live_key_candidates() -> list[str]:
    """Keys for Gemini Live mouth/mic — Live pool first, then general Gemini keys."""
    live = parse_key_list(
        settings.gemini_live_api_key,
        settings.gemini_live_api_keys,
    )
    if live:
        # Live keys first; fall back to general pool for failover.
        return parse_key_list(
            settings.gemini_live_api_key,
            settings.gemini_live_api_keys,
            settings.gemini_api_key,
            settings.gemini_api_key_backup,
            settings.gemini_api_keys,
        )
    return gemini_key_candidates()


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
