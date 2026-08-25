"""Provider registry + model listing for Client BYOK dashboard."""

from __future__ import annotations

import json
from typing import Literal, TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ProviderKind = Literal[
    "gemini",
    "groq",
    "openai",
    "anthropic",
    # Local / self-hosted OpenAI-compatible endpoints.
    "ollama",
    "vllm",
    "llamacpp",
    # Hosted OpenAI-compatible routers.
    "openrouter",
    "huggingface",
]

PROVIDER_CONSOLE: dict[ProviderKind, dict[str, str]] = {
    "gemini": {
        "label": "Google Gemini",
        "console_url": "https://aistudio.google.com/apikey",
        "hint": "Create an API key named Navigator AI in Google AI Studio.",
    },
    "groq": {
        "label": "Groq",
        "console_url": "https://console.groq.com/keys",
        "hint": "Create an API key named Navigator AI in Groq Console.",
    },
    "openai": {
        "label": "OpenAI",
        "console_url": "https://platform.openai.com/api-keys",
        "hint": "Create an API key named Navigator AI in OpenAI Platform.",
    },
    "anthropic": {
        "label": "Anthropic",
        "console_url": "https://console.anthropic.com/settings/keys",
        "hint": "Create an API key named Navigator AI in Anthropic Console.",
    },
    "ollama": {
        "label": "Ollama (local)",
        "console_url": "",
        "hint": "Set Ollama base URL in Settings (default: http://localhost:11434).",
    },
    "vllm": {
        "label": "vLLM (local)",
        "console_url": "",
        "hint": "Set vLLM OpenAI base URL in Settings (default: http://localhost:8000/v1).",
    },
    "llamacpp": {
        "label": "llama.cpp (local)",
        "console_url": "",
        "hint": "Set llama.cpp OpenAI base URL in Settings (default: http://localhost:8000/v1).",
    },
    "openrouter": {
        "label": "OpenRouter",
        "console_url": "https://openrouter.ai/keys",
        "hint": "Create an API key in OpenRouter, paste it below.",
    },
    "huggingface": {
        "label": "Hugging Face Inference Providers",
        "console_url": "https://huggingface.co/settings/tokens",
        "hint": "Create a fine-grained HF token with Inference Providers access, paste it below.",
    },
}

# ponytail: static fallback when list API unavailable
_STATIC_MODELS: dict[ProviderKind, list[tuple[str, str, list[str]]]] = {
    "openai": [
        ("gpt-4o", "GPT-4o", ["chat", "vision"]),
        ("gpt-4o-mini", "GPT-4o mini", ["chat", "vision"]),
        ("gpt-4.1", "GPT-4.1", ["chat"]),
        ("gpt-4.1-mini", "GPT-4.1 mini", ["chat"]),
        ("o3", "o3", ["chat"]),
        ("o3-mini", "o3 mini", ["chat"]),
    ],
    "anthropic": [
        ("claude-opus-4-6", "Claude Opus 4.6", ["chat"]),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6", ["chat"]),
        ("claude-haiku-4-5", "Claude Haiku 4.5", ["chat"]),
    ],
    "gemini": [],
    "groq": [],
    "ollama": [],
    "vllm": [],
    "llamacpp": [],
    "openrouter": [],
    "huggingface": [],
}


class ProviderModel(TypedDict):
    id: str
    label: str
    tags: list[str]


def _gemini_id(name: str) -> str:
    from navigator.core.gemini_keys import gemini_model_bare_id

    return gemini_model_bare_id(name)


# Not used by Navigator agent roles (brain / live / vision-chat).
_GEMINI_NON_AGENT_MARKERS: tuple[str, ...] = (
    "embedding",
    "imagen",
    "veo",
    "aqa",
    "gecko",
    "robotics",
    "tts",
    "-image",
    "omni-flash",
)

_GEMINI_USABLE_ACTIONS: frozenset[str] = frozenset(
    {
        "generateContent",
        "generate_content",
        "bidiGenerateContent",
        "bidi_generate_content",
    }
)


def _tag_gemini(model_id: str) -> list[str]:
    low = model_id.lower()
    tags: list[str] = []
    if "live" in low:
        tags.append("live")
    if "flash" in low or "pro" in low:
        tags.append("chat")
    if "vision" in low:
        tags.append("vision")
    if not tags:
        tags.append("chat")
    return tags


def _gemini_dashboard_model(
    *,
    model_id: str,
    description: str | None,
    actions: list[str] | None,
) -> bool:
    """Keep only served, agent-usable Gemini models for Settings dropdowns."""
    from navigator.core.gemini_keys import (
        gemini_description_deprecated,
        is_gemini_model_served,
    )

    if not model_id or not is_gemini_model_served(model_id):
        return False
    if gemini_description_deprecated(description):
        return False
    low = model_id.lower()
    if any(marker in low for marker in _GEMINI_NON_AGENT_MARKERS):
        return False
    if actions:
        if not _GEMINI_USABLE_ACTIONS.intersection(actions):
            return False
    return True


def list_gemini_models(api_key: str) -> list[ProviderModel]:
    from google import genai

    client = genai.Client(api_key=api_key.strip())
    out: list[ProviderModel] = []
    seen: set[str] = set()
    for item in client.models.list():
        model_id = _gemini_id(getattr(item, "name", "") or "")
        if not model_id or model_id in seen:
            continue
        actions = list(getattr(item, "supported_actions", None) or [])
        description = getattr(item, "description", None)
        if not _gemini_dashboard_model(
            model_id=model_id,
            description=description if isinstance(description, str) else None,
            actions=actions,
        ):
            continue
        seen.add(model_id)
        label = (getattr(item, "display_name", None) or model_id).strip()
        out.append({"id": model_id, "label": label, "tags": _tag_gemini(model_id)})
    out.sort(key=lambda m: (0 if "live" in m["tags"] else 1, m["id"]))
    return out


def _tag_groq(model_id: str) -> list[str]:
    low = model_id.lower()
    if "whisper" in low:
        return ["stt"]
    return ["chat"]


def list_groq_models(api_key: str) -> list[ProviderModel]:
    from navigator.core.groq_client import groq_client

    client = groq_client(api_key.strip())
    out: list[ProviderModel] = []
    seen: set[str] = set()
    resp = client.models.list()
    for item in resp.data:
        model_id = (getattr(item, "id", None) or "").strip()
        if not model_id or model_id in seen:
            continue
        if _id_looks_deprecated(model_id):
            continue
        seen.add(model_id)
        out.append({"id": model_id, "label": model_id, "tags": _tag_groq(model_id)})
    out.sort(key=lambda m: (0 if "chat" in m["tags"] else 1, m["id"]))
    return out


def _id_looks_deprecated(model_id: str, *extra: str) -> bool:
    blob = " ".join((model_id, *extra)).lower()
    return any(
        marker in blob
        for marker in (
            "deprecated",
            "no longer available",
            "shut down",
            "retired",
        )
    )


def _tag_openai(model_id: str) -> list[str]:
    low = model_id.lower()
    tags: list[str] = []
    if "whisper" in low:
        tags.append("stt")
    if "tts" in low or "audio" in low:
        tags.append("live")
    if "gpt" in low or low.startswith("o"):
        tags.append("chat")
    if not tags:
        tags.append("chat")
    return tags


def _tag_openai_compatible(model_id: str) -> list[str]:
    """Best-effort tags for OpenAI-compatible model IDs."""
    low = (model_id or "").lower()
    tags: list[str] = []
    if "whisper" in low:
        tags.append("stt")
    if any(x in low for x in ("vision", "image")):
        tags.append("vision")
    # "chat" is the default: planning/phrasing/classifier are all chat-completions.
    if not tags:
        tags.append("chat")
    return tags


def list_openai_models(api_key: str) -> list[ProviderModel]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key.strip())
    out: list[ProviderModel] = []
    seen: set[str] = set()
    try:
        for item in client.models.list():
            model_id = (getattr(item, "id", None) or "").strip()
            if not model_id or model_id in seen:
                continue
            if _id_looks_deprecated(model_id):
                continue
            seen.add(model_id)
            out.append(
                {"id": model_id, "label": model_id, "tags": _tag_openai(model_id)}
            )
    except Exception:
        out = []
    if not out:
        out = [
            {"id": mid, "label": label, "tags": tags}
            for mid, label, tags in _STATIC_MODELS["openai"]
        ]
    out.sort(key=lambda m: (0 if "chat" in m["tags"] else 1, m["id"]))
    return out


def _tag_anthropic(model_id: str) -> list[str]:
    return ["chat"]


def list_anthropic_models(api_key: str) -> list[ProviderModel]:
    key = api_key.strip()
    out: list[ProviderModel] = []
    try:
        req = Request(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read() or b"{}")
        for item in payload.get("data") or []:
            model_id = (item.get("id") or "").strip()
            if not model_id:
                continue
            label = (item.get("display_name") or model_id).strip()
            if _id_looks_deprecated(model_id, label):
                continue
            out.append({"id": model_id, "label": label, "tags": _tag_anthropic(model_id)})
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError):
        out = []
    if not out:
        out = [
            {"id": mid, "label": label, "tags": tags}
            for mid, label, tags in _STATIC_MODELS["anthropic"]
        ]
    return out


def _normalize_base_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    # Strip trailing slash so "/models" doesn't become "//models".
    return raw[:-1] if raw.endswith("/") else raw


def list_ollama_models(base_url: str) -> list[ProviderModel]:
    """List models from an Ollama server using GET /api/tags."""
    url = _normalize_base_url(base_url)
    if not url:
        raise ValueError("ollama base_url required")
    req = Request(f"{url}/api/tags")
    try:
        with urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read() or b"{}")
        models = payload.get("models") or []
        out: list[ProviderModel] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            model_id = (item.get("model") or item.get("name") or "").strip()
            if not model_id:
                continue
            label = model_id
            out.append({"id": model_id, "label": label, "tags": ["chat"]})
        # Stable-ish sort: chat first (only tag currently) then id.
        out.sort(key=lambda m: m["id"])
        return out
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Could not list Ollama models: {exc}") from None


def list_openai_compatible_models(
    *,
    api_key: str | None,
    base_url: str,
) -> list[ProviderModel]:
    """List models from OpenAI-compatible servers using GET {base_url}/models.

    `base_url` should be the OpenAI SDK base, typically ending in `/v1`
    (example: `http://localhost:8000/v1`).
    """
    url = _normalize_base_url(base_url)
    if not url:
        raise ValueError("base_url required")
    req_headers: dict[str, str] = {}
    key = (api_key or "").strip()
    if key:
        req_headers["Authorization"] = f"Bearer {key}"
    req = Request(f"{url}/models", headers=req_headers)
    try:
        with urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read() or b"{}")
        data = payload.get("data") or []
        out: list[ProviderModel] = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = (item.get("id") or "").strip()
            if not model_id or model_id in seen:
                continue
            if _id_looks_deprecated(model_id):
                continue
            seen.add(model_id)
            out.append(
                {
                    "id": model_id,
                    "label": model_id,
                    "tags": _tag_openai_compatible(model_id),
                }
            )
        # Prefer chat models to make role tag selection deterministic.
        out.sort(key=lambda m: (0 if "chat" in m["tags"] else 1, m["id"]))
        return out
    except Exception as exc:  # noqa: BLE001
        # ponytail: fallback on list outage
        return []


def list_provider_models(
    provider: ProviderKind,
    api_key: str = "",
    *,
    base_url: str = "",
) -> list[ProviderModel]:
    key = (api_key or "").strip()

    if provider == "gemini":
        if not key:
            raise ValueError("API key required")
        return list_gemini_models(key)
    if provider == "groq":
        if not key:
            raise ValueError("API key required")
        return list_groq_models(key)
    if provider == "openai":
        if not key:
            raise ValueError("API key required")
        return list_openai_models(key)
    if provider == "anthropic":
        if not key:
            raise ValueError("API key required")
        return list_anthropic_models(key)

    if provider == "ollama":
        if not base_url:
            raise ValueError("ollama base_url required")
        return list_ollama_models(base_url)

    if provider == "vllm":
        # Local OpenAI-compatible server; key optional.
        return list_openai_compatible_models(api_key=key or None, base_url=base_url)

    if provider == "llamacpp":
        return list_openai_compatible_models(api_key=key or None, base_url=base_url)

    if provider == "openrouter":
        if not key:
            raise ValueError("API key required")
        return list_openai_compatible_models(
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
        )

    if provider == "huggingface":
        if not key:
            raise ValueError("API key required")
        return list_openai_compatible_models(
            api_key=key,
            base_url="https://router.huggingface.co/v1",
        )

    raise ValueError(f"unsupported provider: {provider}")
