"""Role model resolution for runtime."""

from __future__ import annotations

from navigator.core.agent_settings import AgentSettings, merge_agent_settings
from navigator.core.role_models import resolved_runtime_models


def test_role_models_defaults_when_blank():
    settings = merge_agent_settings("{}")
    assert settings.role_brain_provider == "gemini"
    assert settings.role_speaking_provider == "gemini"
    assert settings.role_hands_provider == "groq"
    assert "flash" in settings.role_brain_model.lower()
    assert "live" in settings.role_speaking_model.lower()
    models = resolved_runtime_models(settings)
    assert models["role_hands_provider"] == "groq"
    assert models["live_conversational_model"] == settings.role_speaking_model


def test_role_models_override_legacy():
    settings = merge_agent_settings(
        '{"role_brain_model":"gpt-4o","brain_reasoning_model":"gemini-3.6-flash",'
        '"role_speaking_model":"gemini-live","live_conversational_model":"old-live",'
        '"role_hands_model":"llama-3.3-70b-versatile","role_listening_model":"whisper-large-v3"}'
    )
    models = resolved_runtime_models(settings)
    assert models["brain_reasoning_model"] == "gpt-4o"
    assert models["live_conversational_model"] == "gemini-live"
    assert models["brain_planning_model"] == "llama-3.3-70b-versatile"
    assert models["brain_stt_model"] == "whisper-large-v3"


def test_retired_gemini_flash_remapped():
    settings = merge_agent_settings(
        '{"role_brain_model":"gemini-2.0-flash",'
        '"brain_vision_text_model":"models/gemini-2.0-flash"}'
    )
    models = resolved_runtime_models(settings)
    assert models["brain_reasoning_model"] == "gemini-3.6-flash"
    assert models["brain_vision_text_model"] == "gemini-3.6-flash"


def test_legacy_stt_when_listening_role_overridden():
    settings = merge_agent_settings('{"role_listening_model":"whisper-x"}')
    models = resolved_runtime_models(settings)
    assert models["brain_stt_model"] == "whisper-x"
