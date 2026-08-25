"""Request/response Pydantic models for the Navigator API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from navigator.app.registry import SiteGraphSource
from navigator.app.runner import DemoOrigin
from navigator.meeting.providers import Platform as MeetingPlatform

class IntakePrefill(BaseModel):
    """What the landing page already knows, so the bot needn't ask again."""
    name: str = ""
    company: str = ""
    business_type: str = ""
    looking_for: str = ""

class SiteGraphUpload(BaseModel):
    yaml: str = Field(min_length=1)
    source: SiteGraphSource = "yaml"
    publish: bool = False
    """Default false: an upload is a draft until the Client publishes it."""

class NewDemo(BaseModel):
    page_id: str
    flow_id: str
    meeting_url: str | None = None
    """Ignored here -- POST /v1/demos runs headless, no meeting. See
    POST /v1/demos/start, which creates its own link."""

class DemoView(BaseModel):
    demo_id: UUID
    product_id: str
    revision: int
    session_id: UUID
    origin: DemoOrigin
    status: str
    page_id: str
    actions: int
    failures: int
    error: str | None = None
    said: list[str] = Field(default_factory=list)
    meeting_url: str | None = None
    platform: str | None = None
    bot_in_meeting: bool = False
    leave_grace_remaining: int | None = None
    language: str = "en"
    language_code: str = "en"
    language_confidence: float = 1.0
    current_narration: str = ""
    speech_status: str = "idle"

class DemoRunView(BaseModel):
    """Persisted demo run meta for the client Logs panel (7-day window)."""

    session_id: UUID
    demo_id: UUID
    product_id: str
    platform: str
    status: str
    origin: DemoOrigin = "dashboard_test"
    host_os: str = ""
    host_release: str = ""
    host_machine: str = ""
    host_name: str = ""
    browser: str = ""
    meeting_label: str = ""
    started_at: datetime
    ended_at: datetime | None = None
    fail_count: int = 0

class SessionTokenRequest(BaseModel):
    intake: IntakePrefill | None = None
    expires_in_seconds: int = Field(default=3600, ge=60, le=86400)

class SessionTokenResponse(BaseModel):
    token: str
    expires_at: str
    product_id: str

class StartLiveDemo(BaseModel):
    platform: MeetingPlatform | None = None
    """None -> NAVIGATOR_MEETING_PLATFORM."""
    topic: str | None = None
    page_id: str | None = None
    flow_id: str | None = None
    """None -> NAVIGATOR_LIVE_WALKTHROUGH_FLOW."""
    intake: IntakePrefill | None = None
    auto_play: bool = True
    """When True, finish one playlist flow then continue to the next."""

class MeetingOut(BaseModel):
    url: str
    platform: str
    provider_id: str
    passcode: str = ""
    open_access: bool = False
    """True when the link admits anyone directly -- Navigator can join first
    with nobody to let it out of the waiting room."""

class LiveDemoView(DemoView):
    meeting: MeetingOut

class SystemMetrics(BaseModel):
    host_label: str
    uptime_s: float
    cpu_percent: float
    cpu_count: int
    memory_percent: float
    memory_used_mb: float
    memory_total_mb: float
    net_sent_bytes: int
    net_recv_bytes: int
    gpu: dict[str, Any]
    services: list[dict[str, str]]
    processes: list[dict[str, str]]
    health: list[dict[str, Any]]
    token_usage: dict[str, Any] | None = None

class KnowledgeIngestBody(BaseModel):
    text: str = Field(min_length=1)

class ProductDomainBody(BaseModel):
    base_url: str = Field(min_length=1)

class Tier2Body(BaseModel):
    enabled: bool

class AutonomyModeBody(BaseModel):
    mode: str = Field(pattern="^(guided|adaptive|explorer)$")

class HandoffWebhookBody(BaseModel):
    url: str = ""

class AgentSettingsBody(BaseModel):
    default_language: str | None = None
    extra_languages: list[str] | None = None
    agent_gender: str | None = None
    agent_name: str | None = None
    tone: str | None = None
    gemini_voice: str | None = None
    live_conversational_model: str | None = None
    brain_reasoning_model: str | None = None
    brain_planning_model: str | None = None
    brain_phrasing_model: str | None = None
    brain_classify_model: str | None = None
    brain_stt_model: str | None = None
    brain_vision_text_model: str | None = None
    brain_vision_image_model: str | None = None
    role_brain_provider: str | None = None
    role_brain_model: str | None = None
    role_listening_provider: str | None = None
    role_listening_model: str | None = None
    role_speaking_provider: str | None = None
    role_speaking_model: str | None = None
    role_hands_provider: str | None = None
    role_hands_model: str | None = None
    ollama_base_url: str | None = None
    vllm_base_url: str | None = None
    llamacpp_base_url: str | None = None

class AgentProviderKeysBody(BaseModel):
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None
    huggingface_api_key: str | None = None

class ProviderModelsBody(BaseModel):
    provider: str = Field(
        pattern="^(gemini|groq|openai|anthropic|ollama|vllm|llamacpp|openrouter|huggingface)$"
    )
    api_key: str | None = None
    base_url: str | None = None

class ProductLoginBody(BaseModel):
    login_url: str = ""
    username: str = ""
    #: None = keep stored password; "" = clear; str = replace.
    password: str | None = None
    include_login_in_default_flow: bool = False

class BioBody(BaseModel):
    fields: list[dict[str, str]]

class KnowledgeBody(BaseModel):
    markdown: str = ""

class FlowsBody(BaseModel):
    playlist: list[dict]

class RecordStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_url: str = Field(min_length=1)
    flow_name: str = Field(min_length=1)
    flow_id: str | None = None
    page_id: str = "dashboard"
    narrate: bool = False
    """Show the mic widget in the recorded page and transcribe the walkthrough."""
    save_mode: str = Field(default="new", pattern="^(new|update)$")
    target_flow_id: str | None = None
    target_flow_name: str | None = None

class SiteGraphBody(BaseModel):
    yaml: str = Field(min_length=1)

class DemoScriptPatchBody(BaseModel):
    beats: list[dict[str, Any]] = Field(default_factory=list)

class ProductExploreStartBody(BaseModel):
    start_url: str = ""

class FlowDeleteBody(BaseModel):
    flow_id: str = Field(min_length=1)
    page_id: str | None = None

class FlowSemanticsBody(BaseModel):
    flow_id: str = Field(min_length=1)
    purpose: str | None = None
    tags: list[str] | None = None
    triggers: list[str] | None = None
    auto_name: str | None = None

class DecisionTraceView(BaseModel):
    id: str
    session_id: str
    utterance: str
    branch: str
    chosen_flow_id: str | None = None
    spoken: str
    flow_candidates: list[list[float | str]] = Field(default_factory=list)
    knowledge_hits: list[list[float | str]] = Field(default_factory=list)
    detail: str = ""
    created_at: str
