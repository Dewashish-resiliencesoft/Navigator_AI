"""Unified brain configuration for live demo LLM/STT/TTS paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from navigator.core.settings import settings

AutonomyMode = Literal["guided", "adaptive", "explorer"]


@dataclass(frozen=True)
class BrainConfig:
    groq_api_key: str | None
    gemini_api_key: str | None
    planning_model: str
    phrasing_model: str
    classify_model: str
    stt_model: str
    vision_text_model: str
    vision_image_model: str
    autonomy_mode: AutonomyMode
    listen_timeout_s: float
    resume_silence_s: float
    tier2_enabled: bool
    use_turn_brain: bool
    allow_ephemeral_nav: bool
    guardrail_strict: bool

    @classmethod
    def from_settings(
        cls,
        *,
        autonomy_mode: AutonomyMode = "guided",
        tier2_legacy: bool | None = None,
    ) -> BrainConfig:
        mode = autonomy_mode
        if tier2_legacy is True and mode == "guided":
            mode = "adaptive"

        tier2 = mode in {"adaptive", "explorer"}
        if tier2_legacy is False and mode == "adaptive":
            tier2 = False

        return cls(
            groq_api_key=settings.groq_api_key or None,
            gemini_api_key=settings.gemini_api_key or None,
            planning_model=settings.brain_planning_model,
            phrasing_model=settings.brain_phrasing_model,
            classify_model=settings.brain_classify_model,
            stt_model=settings.brain_stt_model,
            vision_text_model=settings.brain_vision_text_model,
            vision_image_model=settings.brain_vision_image_model,
            autonomy_mode=mode,
            listen_timeout_s=settings.brain_listen_timeout_s,
            resume_silence_s=settings.brain_resume_silence_s,
            tier2_enabled=tier2,
            use_turn_brain=mode == "explorer" or bool(settings.gemini_api_key),
            allow_ephemeral_nav=mode == "explorer",
            guardrail_strict=mode != "explorer",
        )


def pacing_resume_silence(pacing: str, cfg: BrainConfig) -> float:
    if pacing == "rushed":
        return max(3.0, cfg.resume_silence_s * 0.5)
    if pacing == "confused":
        return cfg.resume_silence_s * 1.8
    return cfg.resume_silence_s
