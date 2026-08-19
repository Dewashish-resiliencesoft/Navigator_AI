"""Map Client role picks (brain/listening/speaking/hands) to runtime model ids."""

from __future__ import annotations

from typing import Literal

from navigator.core.agent_settings import (
    DEFAULT_ROLE_BRAIN_MODEL,
    DEFAULT_ROLE_BRAIN_PROVIDER,
    DEFAULT_ROLE_HANDS_MODEL,
    DEFAULT_ROLE_HANDS_PROVIDER,
    DEFAULT_ROLE_LISTENING_MODEL,
    DEFAULT_ROLE_LISTENING_PROVIDER,
    DEFAULT_ROLE_SPEAKING_MODEL,
    DEFAULT_ROLE_SPEAKING_PROVIDER,
    AgentSettings,
)
from navigator.core.settings import settings

RoleName = Literal["brain", "listening", "speaking", "hands"]


def _pick(role_model: str, legacy: str, default: str) -> str:
    role = (role_model or "").strip()
    if role:
        return role
    legacy = (legacy or "").strip()
    if legacy:
        return legacy
    return default


def _provider(role_provider: str, default: str) -> str:
    p = (role_provider or "").strip()
    return p or default


def resolved_runtime_models(settings: AgentSettings) -> dict[str, str]:
    """Role assignments win over legacy fields; blanks use platform role defaults."""
    s = settings.with_role_defaults()
    return {
        "live_conversational_model": _pick(
            s.role_speaking_model,
            s.live_conversational_model,
            DEFAULT_ROLE_SPEAKING_MODEL or settings.live_conversational_model,
        ),
        "brain_reasoning_model": _pick(
            s.role_brain_model,
            s.brain_reasoning_model,
            DEFAULT_ROLE_BRAIN_MODEL or settings.brain_reasoning_model,
        ),
        "brain_planning_model": _pick(
            s.role_hands_model,
            s.brain_planning_model,
            DEFAULT_ROLE_HANDS_MODEL or settings.brain_planning_model,
        ),
        "brain_stt_model": _pick(
            s.role_listening_model,
            s.brain_stt_model,
            settings.brain_stt_model,
        ),
        "brain_phrasing_model": s.brain_phrasing_model or settings.brain_phrasing_model,
        "brain_classify_model": s.brain_classify_model or settings.brain_classify_model,
        "brain_vision_text_model": s.brain_vision_text_model or settings.brain_vision_text_model,
        "brain_vision_image_model": s.brain_vision_image_model or settings.brain_vision_image_model,
        "role_brain_provider": _provider(s.role_brain_provider, DEFAULT_ROLE_BRAIN_PROVIDER),
        "role_listening_provider": _provider(
            s.role_listening_provider, DEFAULT_ROLE_LISTENING_PROVIDER
        ),
        "role_speaking_provider": _provider(
            s.role_speaking_provider, DEFAULT_ROLE_SPEAKING_PROVIDER
        ),
        "role_hands_provider": _provider(s.role_hands_provider, DEFAULT_ROLE_HANDS_PROVIDER),
    }
