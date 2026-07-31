"""Offline LLM access for reflection and vision verification.

Two providers behind one protocol because the cost profiles differ sharply:
Gemini 2.5 Flash has a free tier that includes image input, while OpenAI has no
free tier at all. Free is the default; flip NAVIGATOR_REFLECT_PROVIDER=openai to
switch. Nothing above this module knows which one is in use.

Live conversational calls do NOT go through here -- those are Groq, and they live
in the PLANNING node where latency is the constraint.
"""

from __future__ import annotations

from typing import Protocol

from navigator.settings import settings


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> str:
        """Text in, text out. Used by REFLECTING."""
        ...

    def complete_with_image(self, system: str, user: str, png: bytes) -> str:
        """Same, plus a screenshot. Used by the ambiguous-postcondition fallback."""
        ...


class GeminiProvider:
    """Free tier: ~10 RPM / 250 RPD on 2.5 Flash, image input included.

    Reflection is batched post-call and vision verify is rare, so those limits are
    generous for this workload.
    """

    text_model = "gemini-2.5-flash"
    vision_model = "gemini-2.5-flash"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def complete(self, system: str, user: str) -> str:
        # TODO(phase 4): google.genai Client.models.generate_content with
        # config=GenerateContentConfig(system_instruction=system).
        raise NotImplementedError("Gemini reflection lands in Phase 4")

    def complete_with_image(self, system: str, user: str, png: bytes) -> str:
        # TODO(phase 4): same call with types.Part.from_bytes(png, "image/png").
        raise NotImplementedError("Gemini vision verify lands in Phase 4")


class OpenAIProvider:
    """Paid. Aliases, not dated snapshots -- gpt-4o-2024-05-13 retires 2026-10-23."""

    text_model = "gpt-4o-mini"
    vision_model = "gpt-4o"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def complete(self, system: str, user: str) -> str:
        # TODO(phase 4): openai.OpenAI().chat.completions.create, system + user roles.
        raise NotImplementedError("OpenAI reflection lands in Phase 4")

    def complete_with_image(self, system: str, user: str, png: bytes) -> str:
        # TODO(phase 4): image_url content part with a base64 data URI.
        raise NotImplementedError("OpenAI vision verify lands in Phase 4")


def get_provider() -> LLMProvider:
    """The configured provider. Fails loudly on a missing key rather than at
    the first call, deep inside a reflection batch."""
    if settings.reflect_provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError("NAVIGATOR_GEMINI_API_KEY is not set")
        return GeminiProvider(settings.gemini_api_key)

    if not settings.openai_api_key:
        raise RuntimeError("NAVIGATOR_OPENAI_API_KEY is not set")
    return OpenAIProvider(settings.openai_api_key)
