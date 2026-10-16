"""Gemini primary/backup key helpers."""

from __future__ import annotations

import pytest

from navigator.core import gemini_keys


def test_gemini_key_candidates_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gemini_keys.settings,
        "gemini_api_key",
        "primary-key",
        raising=False,
    )
    monkeypatch.setattr(
        gemini_keys.settings,
        "gemini_api_key_backup",
        "backup-key",
        raising=False,
    )
    monkeypatch.setattr(
        gemini_keys.settings,
        "gemini_api_keys",
        "backup-key,third-key",
        raising=False,
    )
    assert gemini_keys.gemini_key_candidates() == [
        "primary-key",
        "backup-key",
        "third-key",
    ]


def test_gemini_live_key_candidates_prefer_live_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gemini_keys.settings, "gemini_live_api_key", "live-only", raising=False
    )
    monkeypatch.setattr(
        gemini_keys.settings, "gemini_live_api_keys", "", raising=False
    )
    monkeypatch.setattr(
        gemini_keys.settings, "gemini_api_key", "general", raising=False
    )
    monkeypatch.setattr(
        gemini_keys.settings, "gemini_api_key_backup", "", raising=False
    )
    monkeypatch.setattr(gemini_keys.settings, "gemini_api_keys", "", raising=False)
    assert gemini_keys.gemini_live_key_candidates() == ["live-only", "general"]


def test_is_gemini_quota_error() -> None:
    assert gemini_keys.is_gemini_quota_error(
        RuntimeError("429 RESOURCE_EXHAUSTED limit: 0")
    )
    assert not gemini_keys.is_gemini_quota_error(RuntimeError("network timeout"))


def test_is_gemini_live_unavailable() -> None:
    assert gemini_keys.is_gemini_live_unavailable(
        RuntimeError("1011 None. The service is currently unavailable.")
    )
    assert gemini_keys.is_gemini_live_unavailable(RuntimeError("503 overloaded"))
    assert not gemini_keys.is_gemini_live_unavailable(RuntimeError("invalid api key"))


def test_normalize_gemini_model_remaps_retired_flash() -> None:
    assert gemini_keys.normalize_gemini_model("gemini-2.0-flash") == "gemini-3.6-flash"
    assert (
        gemini_keys.normalize_gemini_model("models/gemini-2.0-flash")
        == "gemini-3.6-flash"
    )
    assert gemini_keys.normalize_gemini_model("gemini-3.6-flash") == "gemini-3.6-flash"


def test_is_gemini_model_served_drops_shutdown_families() -> None:
    assert not gemini_keys.is_gemini_model_served("gemini-2.0-flash")
    assert not gemini_keys.is_gemini_model_served("models/gemini-1.5-pro")
    assert gemini_keys.is_gemini_model_served("gemini-2.5-flash")
    assert gemini_keys.is_gemini_model_served("gemini-3.6-flash")
    assert gemini_keys.is_gemini_model_served("gemini-3.1-flash-live-preview")


def test_gemini_description_deprecated() -> None:
    assert gemini_keys.gemini_description_deprecated(
        "This model is no longer available."
    )
    assert not gemini_keys.gemini_description_deprecated("Gemini 3.6 Flash")
