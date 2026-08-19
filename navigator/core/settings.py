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
    #: Playwright server WS for *manual record only* (Platform .env, never API).
    #: Empty = launch Chromium on this host (production default).
    record_browser_ws: str = ""
    #: Path token appended to record_browser_ws when the URL has no path.
    record_ws_path: str = ""
    #: Lab only: connect to TCP-peer:3333 when record_browser_ws is empty.
    #: Off in production — never use X-Forwarded-For for this.
    record_local: bool = False
    site_graph: Path = Path("navigator/knowledge/sites/whatsapp_crm.yaml")
    db_path: Path = Path("navigator.db")
    #: Warm female voice for English + Hindi (Gemini Live prebuilt).
    gemini_live_voice: str = "Sulafat"
    #: Gemini Live model. Audio in/out, native VAD, sync tools only.
    live_conversational_model: str = "gemini-3.1-flash-live-preview"
    #: Silence before Live ends the human's turn. Google's default is ~800ms;
    #: under ~300ms a mid-sentence pause reads as end-of-turn.
    live_vad_silence_ms: int = 800
    #: Quiet time after an interruption before the walkthrough resumes.
    live_resume_silence_s: float = 0.8
    default_spoken_language: Literal["en", "hi"] = "en"

    # Phase 2+
    groq_api_key: str = ""
    #: Comma-separated Groq keys for controller/STT/phrasing rotation.
    groq_api_keys: str = ""
    #: Optional separate pool for analysis workloads; falls back to groq_api_keys.
    groq_api_keys_analysis: str = ""
    reflect_provider: Literal["gemini", "openai"] = "gemini"
    gemini_api_key: str = ""
    gemini_api_key_backup: str = ""
    #: Comma-separated Gemini keys (vision, Live TTS, reflection).
    gemini_api_keys: str = ""
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
    #: Meeting SDK / General App Client ID for Attendee web SDK join.
    #: Never reuse the Server-to-Server ``zoom_client_id`` here — that 3712s.
    zoom_sdk_client_id: str = ""
    zoom_sdk_client_secret: str = ""
    #: Zoom user id for create/ZAK (`me` = S2S token owner).
    zoom_user_id: str = "me"
    #: Public origin Attendee can reach for ZAK callback (tunnel or deploy URL).
    public_base_url: str = ""
    #: Local port the Navigator API listens on. Tunnelled for the Zoom ZAK
    #: callback — pointing at the wrong port leaves Zoom waiting for a host.
    api_port: int = 8000
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
    #: Brain model pins (override provider defaults).
    brain_planning_model: str = "llama-3.3-70b-versatile"
    brain_phrasing_model: str = "llama-3.1-8b-instant"
    brain_classify_model: str = "llama-3.1-8b-instant"
    brain_stt_model: str = "whisper-large-v3-turbo"
    brain_vision_text_model: str = "gemini-3.6-flash"
    brain_vision_image_model: str = "gemini-3.6-flash"
    #: Deep reasoning for agent runtime (Flash).
    brain_reasoning_model: str = "gemini-3.6-flash"
    #: Interactive agent runtime: Live → Orchestrator → Flash → Playwright.
    agent_runtime_enabled: bool = True
    brain_listen_timeout_s: float = 8.0
    brain_resume_silence_s: float = 6.0
    #: Bot joins Meet first; link shared only after Navigator is inside.
    live_bot_first: bool = True
    #: Seconds after human join before first spoken greet.
    #: Keep low — 3s felt like the bot was stuck before saying hello.
    live_human_settle_s: float = 0.35
    #: VAD end-of-utterance silence for live Meet STT (ms).
    #: 800ms matches Gemini Live's own VAD — prevents false barge-ins from
    #: mid-sentence pauses being misread as turn-end.
    live_stt_min_silence_ms: int = 800
    #: Max wait for Attendee audio websocket before starting demo anyway. Zoom
    #: can delay this until the host grants recording permission.
    live_audio_ws_wait_s: float = 120.0
    #: Attendee signed-in Google Meet bot (needs Bot Logins in Attendee dashboard).
    google_meet_use_login: bool = False
    #: Product API key for local client dashboard (server-side; never sent to browser).
    client_api_key: str = ""
    #: Redis URL for multi-worker demo state coordination (e.g. redis://localhost:6379/0).
    redis_url: str = ""
    #: JWT secret for client dashboard auth
    jwt_secret: str = "unsafe-default-secret-change-in-prod"
    #: Screenshare relay target frame rate (16 ms ≈ 60 fps).
    target_fps: int = 60
    #: JPEG quality for Meet screenshare frames (1–100). Meet re-encodes the
    #: share anyway, and the encode runs in the same process as the audio
    #: bridge — a lower number buys real headroom for smooth voice.
    screenshot_quality: int = 70


settings = Settings()
