"""Dashboard rollups for LLM token usage (platform vs Client BYOK)."""

from __future__ import annotations

from typing import Any

# Rough typicals for one ~10 min dashboard test demo on platform keys (merged).
TYPICAL_PLATFORM_PER_DEMO: dict[str, int] = {
    "input_tokens": 20_000,
    "output_tokens": 4_000,
    "calls": 37,
}


def uses_byok(byok: dict[str, bool]) -> bool:
    return any(
        byok.get(k)
        for k in ("has_groq_api_key", "has_gemini_api_key", "has_fish_api_key")
    )


def billing_label(byok: dict[str, bool]) -> str:
    flags = [
        byok.get("has_groq_api_key"),
        byok.get("has_gemini_api_key"),
        byok.get("has_fish_api_key"),
    ]
    if not any(flags):
        return "Platform default keys (Navigator environment)"
    if all(flags):
        return "Your API keys (BYOK)"
    parts: list[str] = []
    if byok.get("has_groq_api_key"):
        parts.append("your Groq")
    if byok.get("has_gemini_api_key"):
        parts.append("your Gemini")
    if byok.get("has_fish_api_key"):
        parts.append("your Fish")
    return f"Mixed — {', '.join(parts)} + platform defaults for the rest"


def collect_token_usage_summary(
    *,
    product_id: str,
    vault: Any,
    db_path: str,
    days: int = 14,
) -> dict[str, Any]:
    from navigator.logs.store import ActionLog

    byok = vault.provider_keys_public(product_id)
    with ActionLog(db_path) as log:
        metrics = log.llm_token_metrics(product_id, days=days)
        client_models = log.llm_token_metrics_by_model(
            product_id, days=days, billed_to="client"
        )

    platform_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0}
    client_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0}
    for row in metrics.get("providers") or []:
        bucket = client_totals if row.get("billed_to") == "client" else platform_totals
        bucket["input_tokens"] += int(row.get("input_tokens") or 0)
        bucket["output_tokens"] += int(row.get("output_tokens") or 0)
        bucket["total_tokens"] += int(row.get("total_tokens") or 0)
        bucket["calls"] += int(row.get("calls") or 0)

    return {
        **metrics,
        "byok": byok,
        "uses_byok": uses_byok(byok),
        "billing_label": billing_label(byok),
        "platform": platform_totals,
        "client": client_totals,
        "client_models": client_models,
        "typical_platform_per_demo": TYPICAL_PLATFORM_PER_DEMO,
        "has_usage": int(metrics.get("calls") or 0) > 0,
    }
