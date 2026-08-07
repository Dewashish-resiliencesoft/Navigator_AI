"""Shared API key pool parsing + rotation on rate-limit errors."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")


def parse_key_list(*parts: str) -> list[str]:
    """Split comma-separated env values; dedupe, skip empty."""
    out: list[str] = []
    for part in parts:
        for raw in (part or "").split(","):
            key = raw.strip()
            if key and key not in out:
                out.append(key)
    return out


def is_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate limit" in msg
        or "resource_exhausted" in msg
        or "quota exceeded" in msg
        or "limit: 0" in msg
        or "too many requests" in msg
    )


def call_with_rotation(
    fn: Callable[[str], T],
    keys: Sequence[str],
    *,
    max_attempts: int | None = None,
    label: str = "api",
) -> T:
    """Try ``fn(key)`` across keys; rotate on rate-limit errors."""
    if not keys:
        raise RuntimeError(f"no {label} keys configured")
    attempts = max_attempts if max_attempts is not None else len(keys)
    last_exc: BaseException | None = None
    for i, key in enumerate(list(keys)[:attempts]):
        try:
            return fn(key)
        except Exception as exc:
            last_exc = exc
            if is_rate_limit_error(exc) and i + 1 < len(keys):
                print(
                    f"[{label}] key {i + 1}/{len(keys)} rate limited — rotating",
                    flush=True,
                )
                continue
            raise
    assert last_exc is not None
    raise last_exc
