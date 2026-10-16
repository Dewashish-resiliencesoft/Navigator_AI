"""Tests: Phase 1 - authoritative language resolution."""
from __future__ import annotations

import pytest
from navigator.voice.resolved_language import resolve_language, ResolvedLanguage


def test_session_language_wins():
    r = resolve_language(session_language="hi", agent_settings_language="en", global_default="en")
    assert r.code == "hi"
    assert r.source == "session"


def test_agent_settings_language_wins_over_global():
    r = resolve_language(session_language=None, agent_settings_language="hi", global_default="en")
    assert r.code == "hi"
    assert r.source == "agent_settings"


def test_global_default_used_when_no_override():
    r = resolve_language(session_language=None, agent_settings_language=None, global_default="hi")
    assert r.code == "hi"
    assert r.source == "global_default"


def test_fallback_to_english():
    r = resolve_language(session_language=None, agent_settings_language=None, global_default=None)
    assert r.code == "en"
    assert r.source == "fallback"


def test_invalid_language_value_skipped():
    r = resolve_language(session_language="xx", agent_settings_language="hi", global_default="en")
    # "xx" is invalid → falls through to agent_settings
    assert r.code == "hi"
    assert r.source == "agent_settings"


def test_empty_string_treated_as_none():
    r = resolve_language(session_language="", agent_settings_language="hi", global_default="en")
    assert r.code == "hi"
    assert r.source == "agent_settings"


def test_hindi_default_no_english_fallback():
    """If Hindi is configured, resolved language must be 'hi', never 'en'."""
    r = resolve_language(session_language=None, agent_settings_language="hi", global_default="en")
    assert r.code == "hi", "Hindi configured in settings must produce Hindi, not English fallback"


def test_resolved_language_is_frozen():
    r = resolve_language(session_language="en")
    with pytest.raises((AttributeError, TypeError)):
        r.code = "hi"  # type: ignore[misc]
