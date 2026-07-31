"""Environment-backed configuration. Every knob is an env var; see .env.example."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NAVIGATOR_",
        extra="ignore",
    )

    # Phase 1
    headful: bool = True
    site_graph: Path = Path("navigator/config/sites/whatsapp_crm.yaml")
    db_path: Path = Path("navigator.db")
    piper_voice: str = "en_US-lessac-medium"
    piper_data_dir: Path = Path("voices")

    # Phase 2+
    groq_api_key: str = ""
    reflect_provider: Literal["gemini", "openai"] = "gemini"
    gemini_api_key: str = ""
    openai_api_key: str = ""
    chroma_path: Path = Path("chroma")
    attendee_base_url: str = "http://localhost:8000/api/v1"
    attendee_api_key: str = ""


settings = Settings()
