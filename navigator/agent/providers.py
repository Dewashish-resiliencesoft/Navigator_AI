"""Offline LLM access for reflection and vision verification.

Two providers behind one protocol because the cost profiles differ sharply:
Gemini 2.5 Flash has a free tier that includes image input, while OpenAI has no
free tier at all. Free is the default; flip NAVIGATOR_REFLECT_PROVIDER=openai to
switch. Nothing above this module knows which one is in use.

Live conversational calls do NOT go through here -- those are Groq, and they live
in the PLANNING node where latency is the constraint.
"""

from __future__ import annotations

import base64
from typing import Protocol

from navigator.core.settings import settings


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> str:
        """Text in, text out. Used by REFLECTING."""
        ...

    def complete_with_image(self, system: str, user: str, png: bytes) -> str:
        """Same, plus a screenshot. Used by the ambiguous-postcondition fallback."""
        ...


class GeminiProvider:
    """Free tier vision/text; models overridable via settings."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.text_model = settings.brain_vision_text_model
        self.vision_model = settings.brain_vision_image_model

    def complete(self, system: str, user: str) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        resp = client.models.generate_content(
            model=self.text_model,
            contents=user,
            config=types.GenerateContentConfig(system_instruction=system),
        )
        text = (resp.text or "").strip()
        if not text:
            raise RuntimeError("Gemini returned empty reflection")
        return text

    def complete_with_image(self, system: str, user: str, png: bytes) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        resp = client.models.generate_content(
            model=self.vision_model,
            contents=[
                types.Part.from_bytes(data=png, mime_type="image/png"),
                user,
            ],
            config=types.GenerateContentConfig(system_instruction=system),
        )
        text = (resp.text or "").strip()
        if not text:
            raise RuntimeError("Gemini vision returned empty")
        return text


class OpenAIProvider:
    """Paid. Aliases, not dated snapshots."""

    text_model = "gpt-4o-mini"
    vision_model = "gpt-4o"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def complete(self, system: str, user: str) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=self.text_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
        )
        content = resp.choices[0].message.content
        if not content:
            raise RuntimeError("OpenAI returned empty reflection")
        return content.strip()

    def complete_with_image(self, system: str, user: str, png: bytes) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        b64 = base64.b64encode(png).decode()
        resp = client.chat.completions.create(
            model=self.vision_model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                },
            ],
            temperature=0,
        )
        content = resp.choices[0].message.content
        if not content:
            raise RuntimeError("OpenAI vision returned empty")
        return content.strip()


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
