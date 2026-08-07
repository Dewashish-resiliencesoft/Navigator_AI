"""Groq key pool + rotation helpers."""

from __future__ import annotations

import pytest

from navigator.core import groq_keys, key_pool


def test_parse_key_list_dedupes() -> None:
    assert key_pool.parse_key_list("a, b", "b,c") == ["a", "b", "c"]


def test_is_rate_limit_error() -> None:
    assert key_pool.is_rate_limit_error(RuntimeError("429 rate limit"))
    assert not key_pool.is_rate_limit_error(RuntimeError("network timeout"))


def test_call_with_rotation_on_rate_limit() -> None:
    calls: list[str] = []

    def fn(key: str) -> str:
        calls.append(key)
        if key == "bad":
            raise RuntimeError("429 rate limit")
        return "ok"

    out = key_pool.call_with_rotation(fn, ["bad", "good"], label="test")
    assert out == "ok"
    assert calls == ["bad", "good"]


def test_groq_key_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groq_keys.settings, "groq_api_key", "primary", raising=False)
    monkeypatch.setattr(groq_keys.settings, "groq_api_keys", "alt1,alt2", raising=False)
    assert groq_keys.groq_key_candidates() == ["primary", "alt1", "alt2"]
