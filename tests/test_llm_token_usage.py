"""LLM token usage store + billing label rollups."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from navigator.app.token_usage import billing_label, collect_token_usage_summary
from navigator.logs.store import ActionLog


class _FakeVault:
    def __init__(self, byok: dict[str, bool]) -> None:
        self._byok = byok

    def provider_keys_public(self, product_id: str) -> dict:
        return {
            "has_groq_api_key": self._byok.get("groq", False),
            "has_gemini_api_key": self._byok.get("gemini", False),
            "has_fish_api_key": self._byok.get("fish", False),
            "updated_at": None,
        }


@pytest.mark.parametrize(
    ("byok", "expected"),
    [
        ({}, "Platform default keys (Navigator environment)"),
        (
            {"groq": True, "gemini": True, "fish": True},
            "Your API keys (BYOK)",
        ),
        (
            {"groq": True},
            "Mixed — your Groq + platform defaults for the rest",
        ),
    ],
)
def test_billing_label(byok: dict[str, bool], expected: str) -> None:
    flags = {
        "has_groq_api_key": byok.get("groq", False),
        "has_gemini_api_key": byok.get("gemini", False),
        "has_fish_api_key": byok.get("fish", False),
    }
    assert billing_label(flags) == expected


def test_llm_token_metrics_and_summary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "actions.db"
        with ActionLog(str(db)) as log:
            log.record_llm_usage(
                product_id="acme",
                session_id="sess_1",
                provider="groq",
                purpose="phrasing",
                model="llama",
                input_tokens=100,
                output_tokens=20,
                billed_to="platform",
            )
            log.record_llm_usage(
                product_id="acme",
                session_id="sess_1",
                provider="gemini",
                purpose="vision",
                model="flash",
                input_tokens=50,
                output_tokens=10,
                billed_to="client",
            )

        vault = _FakeVault({"gemini": True})
        summary = collect_token_usage_summary(
            product_id="acme",
            vault=vault,
            db_path=str(db),
            days=14,
        )
        assert summary["has_usage"] is True
        assert summary["uses_byok"] is True
        assert summary["platform"]["total_tokens"] == 120
        assert summary["client"]["total_tokens"] == 60
        assert len(summary["client_models"]) == 1
        assert summary["client_models"][0]["model"] == "flash"
        assert summary["billing_label"].startswith("Mixed")
        assert summary["typical_platform_per_demo"]["calls"] == 37


def test_platform_only_merged_typical() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "actions.db"
        vault = _FakeVault({})
        summary = collect_token_usage_summary(
            product_id="acme",
            vault=vault,
            db_path=str(db),
            days=14,
        )
        assert summary["uses_byok"] is False
        assert summary["typical_platform_per_demo"]["input_tokens"] == 20_000
        assert summary["client_models"] == []
