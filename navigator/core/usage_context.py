"""Thread-local LLM usage context for per-tenant token accounting."""

from __future__ import annotations

import threading
from typing import Any

_local = threading.local()


def bind_demo_usage(
    *,
    product_id: str,
    session_id: str | None = None,
    groq_client: bool = False,
    gemini_client: bool = False,
) -> None:
    """Mark which providers bill the Client (BYOK) vs Platform for this demo thread."""
    _local.ctx = {
        "product_id": product_id,
        "session_id": session_id,
        "groq_client": groq_client,
        "gemini_client": gemini_client,
    }


def clear_demo_usage() -> None:
    _local.ctx = None


def current() -> dict[str, Any] | None:
    return getattr(_local, "ctx", None)


def _record(
    *,
    provider: str,
    purpose: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    billed_to: str,
) -> None:
    ctx = current()
    if not ctx or not ctx.get("product_id"):
        return
    if input_tokens <= 0 and output_tokens <= 0:
        return
    from navigator.core.settings import settings
    from navigator.logs.store import ActionLog

    with ActionLog(settings.db_path) as log:
        log.record_llm_usage(
            product_id=str(ctx["product_id"]),
            session_id=ctx.get("session_id"),
            provider=provider,
            purpose=purpose,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            billed_to=billed_to,
        )


def record_groq_chat(resp: Any, *, purpose: str, model: str) -> None:
    ctx = current()
    if not ctx:
        return
    usage = getattr(resp, "usage", None)
    inp = int(getattr(usage, "prompt_tokens", 0) or 0)
    out = int(getattr(usage, "completion_tokens", 0) or 0)
    billed = "client" if ctx.get("groq_client") else "platform"
    _record(
        provider="groq",
        purpose=purpose,
        model=model,
        input_tokens=inp,
        output_tokens=out,
        billed_to=billed,
    )


def record_gemini_generate(resp: Any, *, purpose: str, model: str) -> None:
    ctx = current()
    if not ctx:
        return
    usage = getattr(resp, "usage_metadata", None)
    inp = int(getattr(usage, "prompt_token_count", 0) or 0)
    out = int(getattr(usage, "candidates_token_count", 0) or 0)
    billed = "client" if ctx.get("gemini_client") else "platform"
    _record(
        provider="gemini",
        purpose=purpose,
        model=model,
        input_tokens=inp,
        output_tokens=out,
        billed_to=billed,
    )


def record_openai_chat(resp: Any, *, purpose: str, model: str) -> None:
    ctx = current()
    if not ctx:
        return
    usage = getattr(resp, "usage", None)
    inp = int(getattr(usage, "prompt_tokens", 0) or 0)
    out = int(getattr(usage, "completion_tokens", 0) or 0)
    _record(
        provider="openai",
        purpose=purpose,
        model=model,
        input_tokens=inp,
        output_tokens=out,
        billed_to="platform",
    )
