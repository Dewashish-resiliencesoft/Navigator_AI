"""Per-product agent voice / language / provider settings (Client dashboard)."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SpokenLanguage = Literal["en", "hi"]
AgentGender = Literal["female", "male"]

DEFAULT_AGENT_SETTINGS: dict[str, object] = {
    "default_language": "en",
    "extra_languages": ["hi"],
    "agent_gender": "female",
    "agent_name": "",
    "tone": "",
    "gemini_voice": "",
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
    return AgentSettings.model_validate(data)
