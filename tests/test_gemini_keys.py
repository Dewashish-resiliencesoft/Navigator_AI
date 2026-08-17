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
