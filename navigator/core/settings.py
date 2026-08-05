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
    site_graph: Path = Path("navigator/knowledge/sites/whatsapp_crm.yaml")
    db_path: Path = Path("navigator.db")
    piper_voice: str = "en_US-lessac-medium"
    piper_data_dir: Path = Path("voices")
    #: Fish Audio — main Meet TTS when set (free S2.1 Pro + Sarah).
    fish_api_key: str = ""
    fish_model: str = "s2.1-pro-free"
    #: Default: warm conversational Sarah (fish.audio/m/3a7a3d3df82948c6bd756761d6b139b5)
    fish_reference_id: str = "3a7a3d3df82948c6bd756761d6b139b5"
    #: "auto" | "gemini" | "fish" | "piper" (auto: Gemini → Fish → Piper)
    tts_provider: Literal["auto", "gemini", "fish", "piper"] = "auto"
    gemini_live_model: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    #: Warm female voice for English + Hindi (Gemini Live prebuilt).
    gemini_live_voice: str = "Sulafat"
    default_spoken_language: Literal["en", "hi"] = "en"

    # Phase 2+
    groq_api_key: str = ""
    reflect_provider: Literal["gemini", "openai"] = "gemini"
    gemini_api_key: str = ""
    openai_api_key: str = ""
    chroma_path: Path = Path("chroma")
    #: Self-hosted Attendee. Not 8000 (Navigator's own API) and not 8001
    #: (Attendee's webpage-streamer already publishes there).
    attendee_base_url: str = "http://localhost:8002/api/v1"
    attendee_api_key: str = ""
    #: ``docker compose up -d`` local Attendee on Navigator startup when base URL
    #: is localhost and nothing answers yet. Off for cloud Attendee or pytest.
    attendee_autostart: bool = True
    #: Attendee clone (separate repo). Override with NAVIGATOR_ATTENDEE_COMPOSE_DIR.
    attendee_compose_dir: Path = Path.home() / "projects" / "attendee"

    # Phase 3: Meet + email notify + product login
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
    #: Zoom user id for create/ZAK (`me` = S2S token owner).
    zoom_user_id: str = "me"
    #: Public origin Attendee can reach for ZAK callback (tunnel or deploy URL).
    public_base_url: str = ""
    #: Optional shared secret appended as ?secret= on zoom_tokens_url.
    zoom_zak_callback_secret: str = ""
    product_url: str = ""
    #: Legacy single-tenant product login. Per-Client credentials now live in the
    #: credential vault, keyed by product_id; these remain as a fallback for the
    #: CLI login smoke test and single-tenant local runs.
    product_login_email: str = ""
    product_login_password: str = ""
    #: Fernet key encrypting Clients' product login passwords at rest. No default:
    #: an absent key must fail the save, never silently store plaintext.
    credential_key: str = ""
    credential_db_path: str = "data/credentials.db"
    #: Explore self-heal episodes (JSONL + shots). Auto-purged after 7 days.
    explore_episodes_path: Path = Path("data/explore_episodes")
    #: Inbox that receives the Meet link (Resend auto-send or mailto fallback).
    notify_email: str = ""
    #: Resend API key — https://resend.com (free). When set, email is auto-sent.
    resend_api_key: str = ""
    #: From address. Free tier: onboarding@resend.dev (no domain verify).
    email_from: str = "Navigator AI <onboarding@resend.dev>"
    #: Open Meet in *this* machine's browser. Off by default — demos will run on
    #: a business landing page later; share the link via email instead.
    open_meet_in_browser: bool = False
    tunnel_bin: str = "cloudflared"
    meet_live: bool = False
    live_walkthrough_flow: str = "default_walkthrough"
    live_max_turns: int = 50
    #: Bot joins Meet first; link shared only after Navigator is inside.
    live_bot_first: bool = True
    #: Attendee signed-in Google Meet bot (needs Bot Logins in Attendee dashboard).
    google_meet_use_login: bool = False
    #: Product API key for local client dashboard (server-side; never sent to browser).
    client_api_key: str = ""
    #: Redis URL for multi-worker demo state coordination (e.g. redis://localhost:6379/0).
    redis_url: str = ""
    #: JWT secret for client dashboard auth
    jwt_secret: str = "unsafe-default-secret-change-in-prod"


settings = Settings()
