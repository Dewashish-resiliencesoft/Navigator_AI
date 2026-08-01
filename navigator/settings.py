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
    #: Fish Audio — main Meet TTS when set (free S2.1 Pro + Sarah).
    fish_api_key: str = ""
    fish_model: str = "s2.1-pro-free"
    #: Default: warm conversational Sarah (fish.audio/m/3a7a3d3df82948c6bd756761d6b139b5)
    fish_reference_id: str = "3a7a3d3df82948c6bd756761d6b139b5"
    #: "fish" | "piper" | "auto" (Fish if key set, else Piper)
    tts_provider: Literal["auto", "fish", "piper"] = "auto"

    # Phase 2+
    groq_api_key: str = ""
    reflect_provider: Literal["gemini", "openai"] = "gemini"
    gemini_api_key: str = ""
    openai_api_key: str = ""
    chroma_path: Path = Path("chroma")
    attendee_base_url: str = "http://localhost:8000/api/v1"
    attendee_api_key: str = ""

    # Phase 3: Meet + Teams + product login
    #: Fallback only: the CLI path (`python -m navigator.meeting.live_demo`) uses
    #: this when no URL is passed in. The API creates a fresh link per session.
    meeting_url: str = ""
    #: Which provider mints that per-session link. "static" reuses meeting_url.
    meeting_platform: Literal["google_meet", "zoom", "static"] = "google_meet"
    #: Service account JSON — inline, or a path to the key file. Needs
    #: domain-wide delegation; a bare service account cannot create a Meet space.
    google_sa_json: str = ""
    #: Workspace user the service account impersonates (DWD subject).
    google_impersonate: str = ""
    zoom_account_id: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
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
    live_walkthrough_flow: str = "default_walkthrough"
    live_max_turns: int = 50
    #: Bot joins Meet first; link shared only after Navigator is inside.
    live_bot_first: bool = True
    #: Attendee signed-in Google Meet bot (needs Bot Logins in Attendee dashboard).
    google_meet_use_login: bool = False


settings = Settings()
