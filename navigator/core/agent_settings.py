"""Per-product agent voice / language / provider settings (Client dashboard)."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from navigator.core.settings import settings

SpokenLanguage = Literal["en", "hi"]
AgentGender = Literal["female", "male"]

# Default runtime roles — Gemini Live audio, Gemini Flash brain, Groq hands.
DEFAULT_ROLE_BRAIN_PROVIDER = "gemini"
DEFAULT_ROLE_BRAIN_MODEL = settings.brain_reasoning_model
DEFAULT_ROLE_LISTENING_PROVIDER = "gemini"
DEFAULT_ROLE_LISTENING_MODEL = settings.live_conversational_model
DEFAULT_ROLE_SPEAKING_PROVIDER = "gemini"
DEFAULT_ROLE_SPEAKING_MODEL = settings.live_conversational_model
DEFAULT_ROLE_HANDS_PROVIDER = "groq"
DEFAULT_ROLE_HANDS_MODEL = settings.brain_planning_model

DEFAULT_AGENT_SETTINGS: dict[str, object] = {
    "default_language": "en",
    "extra_languages": ["hi"],
    "agent_gender": "female",
    "agent_name": "",
    "tone": "",
    "gemini_voice": "",
    # Per-product model overrides. Blank keeps provider/server defaults.
    "live_conversational_model": "",
    "brain_reasoning_model": "",
    "brain_planning_model": "",
    "brain_phrasing_model": "",
    "brain_classify_model": "",
    "brain_stt_model": "",
    "brain_vision_text_model": "",
    "brain_vision_image_model": "",
    "role_brain_provider": DEFAULT_ROLE_BRAIN_PROVIDER,
    "role_brain_model": DEFAULT_ROLE_BRAIN_MODEL,
    "role_listening_provider": DEFAULT_ROLE_LISTENING_PROVIDER,
    "role_listening_model": DEFAULT_ROLE_LISTENING_MODEL,
    "role_speaking_provider": DEFAULT_ROLE_SPEAKING_PROVIDER,
    "role_speaking_model": DEFAULT_ROLE_SPEAKING_MODEL,
    "role_hands_provider": DEFAULT_ROLE_HANDS_PROVIDER,
    "role_hands_model": DEFAULT_ROLE_HANDS_MODEL,
    # Local provider endpoints (not secrets). Empty = unconfigured.
    "ollama_base_url": "",
    "vllm_base_url": "",
    "llamacpp_base_url": "",
}

GEMINI_VOICE_BY_GENDER: dict[AgentGender, str] = {
    "female": "Sulafat",
    "male": "Charon",
}


class AgentSettings(BaseModel):
    default_language: SpokenLanguage = "en"
    extra_languages: list[SpokenLanguage] = Field(default_factory=lambda: ["hi"])
    agent_gender: AgentGender = "female"
    agent_name: str = ""
    tone: str = ""
    gemini_voice: str = ""
    #: Gemini Live realtime audio+voice model id override. "" = default.
    live_conversational_model: str = ""
    #: Gemini Flash deep reasoning model id override. "" = default.
    brain_reasoning_model: str = ""
    #: Groq planning model id override. "" = default.
    brain_planning_model: str = ""
    #: Groq phrasing model id override. "" = default.
    brain_phrasing_model: str = ""
    #: Groq classifier model id override. "" = default.
    brain_classify_model: str = ""
    #: Groq STT model id override (non-Live paths). "" = default.
    brain_stt_model: str = ""
    #: Gemini vision text model id override. "" = default.
    brain_vision_text_model: str = ""
    #: Gemini vision image model id override. "" = default.
    brain_vision_image_model: str = ""
    #: Main reasoning brain — provider id (gemini|groq|openai|anthropic).
    role_brain_provider: str = ""
    role_brain_model: str = ""
    #: Ears / STT — what hears the visitor.
    role_listening_provider: str = ""
    role_listening_model: str = ""
    #: Voice / TTS — what speaks in the meeting.
    role_speaking_provider: str = ""
    role_speaking_model: str = ""
    #: Hands — plans browser actions after brain updates.
    role_hands_provider: str = ""
    role_hands_model: str = ""

    #: Local provider endpoints. Used when role provider is `ollama` / `vllm` /
    #: `llamacpp`.
    ollama_base_url: str = ""
    vllm_base_url: str = ""
    llamacpp_base_url: str = ""

    @field_validator("extra_languages", mode="before")
    @classmethod
    def _normalize_langs(cls, value: object) -> list[str]:
        if value is None:
            return ["hi"]
        if not isinstance(value, list):
            return ["hi"]
        out: list[str] = []
        for item in value:
            lang = str(item).strip().lower()
            if lang in {"en", "hi"} and lang not in out:
                out.append(lang)
        return out or ["hi"]

    def allowed_languages(self) -> frozenset[SpokenLanguage]:
        langs: set[SpokenLanguage] = {self.default_language}
        for lang in self.extra_languages:
            langs.add(lang)
        return frozenset(langs)

    def effective_gemini_voice(self) -> str:
        custom = (self.gemini_voice or "").strip()
        if custom:
            return custom
        return GEMINI_VOICE_BY_GENDER.get(self.agent_gender, "Sulafat")

    def with_role_defaults(self) -> AgentSettings:
        """Fill blank role picks with platform defaults (Gemini Live / Flash / Groq)."""
        patch: dict[str, str] = {}
        if not (self.role_brain_provider or "").strip():
            patch["role_brain_provider"] = DEFAULT_ROLE_BRAIN_PROVIDER
        if not (self.role_brain_model or "").strip():
            patch["role_brain_model"] = DEFAULT_ROLE_BRAIN_MODEL
        if not (self.role_listening_provider or "").strip():
            patch["role_listening_provider"] = DEFAULT_ROLE_LISTENING_PROVIDER
        if not (self.role_listening_model or "").strip():
            patch["role_listening_model"] = DEFAULT_ROLE_LISTENING_MODEL
        if not (self.role_speaking_provider or "").strip():
            patch["role_speaking_provider"] = DEFAULT_ROLE_SPEAKING_PROVIDER
        if not (self.role_speaking_model or "").strip():
            patch["role_speaking_model"] = DEFAULT_ROLE_SPEAKING_MODEL
        if not (self.role_hands_provider or "").strip():
            patch["role_hands_provider"] = DEFAULT_ROLE_HANDS_PROVIDER
        if not (self.role_hands_model or "").strip():
            patch["role_hands_model"] = DEFAULT_ROLE_HANDS_MODEL
        if patch:
            return self.model_copy(update=patch)
        return self


def merge_agent_settings(raw: str | None) -> AgentSettings:
    data: dict[str, object] = dict(DEFAULT_AGENT_SETTINGS)
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key in DEFAULT_AGENT_SETTINGS:
                if key in parsed:
                    data[key] = parsed[key]
    return AgentSettings.model_validate(data).with_role_defaults()
