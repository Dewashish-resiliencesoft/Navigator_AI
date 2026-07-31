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

    # Phase 3: Meet + Teams + product login
    meeting_url: str = ""
    product_url: str = ""
    product_login_email: str = ""
    product_login_password: str = ""
    teams_webhook_url: str = ""
    #: Inbox that receives the Meet link (Resend auto-send or mailto fallback).
    notify_email: str = ""
    #: Resend API key — https://resend.com (free). When set, email is auto-sent.
    resend_api_key: str = ""
    #: From address. Free tier: onboarding@resend.dev (no domain verify).
    email_from: str = "Navigator AI <onboarding@resend.dev>"
    #: Open Meet in *this* machine's browser. Off by default — demos will run on
    #: a business landing page later; share the link via email/Teams instead.
    open_meet_in_browser: bool = False
    tunnel_bin: str = "cloudflared"
    meet_live: bool = False


settings = Settings()
