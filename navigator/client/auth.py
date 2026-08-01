"""Persist local client-dashboard API key across uvicorn reloads."""

from __future__ import annotations

from pathlib import Path

# Repo-local; gitignored. Survives --reload when NAVIGATOR_CLIENT_API_KEY unset.
_KEY_FILE = Path(".navigator_client_key")


def load_persisted_client_key() -> str:
    try:
        if _KEY_FILE.is_file():
            return _KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return ""


def persist_client_key(key: str) -> None:
    text = (key or "").strip()
    if not text:
        return
    _KEY_FILE.write_text(text + "\n", encoding="utf-8")


def resolve_client_api_key(settings_key: str) -> str:
    return (settings_key or "").strip() or load_persisted_client_key()
