"""Call state.

Explicit and inspectable by design: at any point in a call you can dump this and
see exactly what the agent believes, what it is about to do, and what has already
failed. Nothing important lives implicitly inside an LLM's context.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, TypedDict
from uuid import UUID

from playwright.sync_api import Page

from navigator.knowledge.site_graph import SiteGraph
from navigator.logs.store import ActionLog
from navigator.core.schemas import ActionLogEntry, Plan, ToolCall, ToolResult
from navigator.voice.tts import Speaker

if TYPE_CHECKING:
    from navigator.meeting.attendee import AttendeeClient
    from navigator.meeting.intake import ProspectIntake


State = Literal[
    "joining",
    "introducing",
    "listening",
    "planning",
    "executing",
    "verifying",
    "speaking",
    "reflecting",
    "ending",
]


@dataclass
class CallDeps:
    """Everything a node needs from the outside world.

    Passed once at graph construction rather than smuggled through state, so state
    stays serialisable and nodes stay unit-testable with a fake.
    """

    graph: SiteGraph
    page: Page
    log: ActionLog
    speaker: Speaker
    #: Phase 1: which flow PLANNING replays. When set, wins over the LLM picker.
    scripted_flow: tuple[str, str] | None = None
    #: Which product is being demoed. Single-tenant in Phase 1; the wrapper API
    #: sets this per demo so one deployment can serve many products.
    product_id: str = "default"
    #: Where ENDING writes transcripts and action dumps. Namespaced by product_id
    #: underneath, so tenants never share a directory.
    archive_dir: Path = Path("archives")
    #: Groq key for the LLM flow picker. None → settings.groq_api_key.
    groq_api_key: str | None = None
    #: Chroma persistence root. None → settings.chroma_path.
    chroma_path: Path | None = None
    #: Injected flow picker for tests. When set, no Groq key is required.
    #: ponytail: one CallDeps field. Ceiling: ad-hoc callable. Upgrade: LLMProvider (Phase 4).
    choose_flow: Callable[..., object] | None = None
    #: Phase 3: Google Meet / Zoom URL. Empty → joining stays standalone.
    meeting_url: str | None = None
    attendee: AttendeeClient | None = None
    #: Public HTTPS URL of the frame relay /view page for Attendee voice agent.
    voice_agent_url: str | None = None
    #: Optional: push a Meet screen-share frame (Playwright thread only).
    push_frame: Callable[[], None] | None = None
    #: Relay `/view` poll counter — speaking uses this to avoid frozen frames.
    get_frame_hits: Callable[[], int] | None = None
    #: When True, LISTENING prompts on stdin for what the prospect said.
    interactive_listen: bool = False
    #: Live Meet bot id (handoff chat + speak into call).
    bot_id: str | None = None
    #: PCM frame iterator for STT (16-bit mono @ 16kHz, ~200ms frames). None → scripted/stdin.
    audio_frames: Iterator[bytes] | None = None
    #: Injected STT for tests. Signature (pcm_utterance: bytes) -> str.
    transcribe_audio: Callable[[bytes], str] | None = None
    #: Reflection LLM (Gemini/OpenAI). None → get_provider() when reflecting.
    reflect_provider: object | None = None
    #: SQLite path for pending corrections. None → settings.db_path.
    pending_db_path: Path | None = None
    #: Pre-demo prospect intake (company / looking_for). None → planner ignores.
    intake: ProspectIntake | None = None
    #: Return True if STT text is the bot hearing itself (skip / keep listening).
    is_bot_echo: Callable[[str], bool] | None = None
    #: Share-overlay status: set_status("listening", "Listening…").
    set_status: Callable[..., None] | None = None
    #: Avatar state for Meet camera tile (speaking, listening, thinking, idle)
    set_avatar_state: Callable[[str], None] | None = None
    #: Live Playwright screen snapshot for planner (url/title/visible text).
    screen_context: Callable[[], str] | None = None
    #: Product expert brief (markdown). Empty → persona-only.
    product_brief: str = ""
    #: During TTS wait: return True to cut remaining playback wait (barge-in).
    check_barge_in: Callable[[], bool] | None = None
    #: Optional hook when prospect speech is captured (prefetch, analytics).
    on_user_utterance: Callable[[str], None] | None = None
    #: Filled when barge-in heard speech; LISTENING consumes it.
    pending_barge_in: list[str] | None = None
    #: Injected Gemini turn brain for tests. When None, use decide_turn if Gemini key set.
    decide_turn: Callable[..., object] | None = None
    #: Prefer Gemini Vision interrupt path when True (default if key present).
    use_turn_brain: bool | None = None
    #: Resolve VAULT_PASSWORD_SENTINEL → plaintext at fill time. Server-side only.
    resolve_password: Callable[[], str | None] | None = None
    #: Current login URL config for session-expiry detection (live, not cached).
    login_config: object | None = None
    #: Mid-demo silent re-auth. Returns True on success. None → no recovery.
    relogin: Callable[[], bool] | None = None
    #: Operator End / human left Meet — nodes should finish without more TTS/clicks.
    stop_event: object | None = None
    #: Call-scoped memory (flows/topics/facts already covered). None → PLANNING
    #: makes one per call, so an existing caller keeps working unchanged.
    memory: object | None = None
    #: SQLite path for DecisionTrace. None → settings.db_path.
    decision_db_path: Path | None = None
    #: Injected retrieval for tests. Signature matches knowledge.retrieve_context.
    retrieve: Callable[..., object] | None = None
    #: Injected phrasing for tests. Signature matches agent.phrasing.phrase_turn.
    phrase: Callable[..., str] | None = None
    #: Spoken language for TTS + phrasing: "en" (default) or "hi".
    spoken_language: Literal["en", "hi"] = "en"
    #: Languages the agent may switch to when the prospect asks.
    extra_languages: tuple[Literal["en", "hi"], ...] = ("hi",)
    #: First-person voice gender — must match TTS voice and Hindi verb forms.
    agent_gender: Literal["female", "male"] = "female"
    #: Mid-step STT for requires_live_input fills. Signature (prompt: str) -> heard.
    listen_once: Callable[[str], str] | None = None
    #: Entity extraction for live fills. Signature (key, question, heard) -> str.
    extract_entity: Callable[..., str] | None = None
    #: Unified brain settings (models, autonomy, listen/resume timeouts).
    brain_config: object | None = None
    #: Client webhook when agent hands off to a human.
    handoff_webhook_url: str = ""
    #: Per-product Tier 2 live fallback. Default OFF — must be explicitly enabled.
    tier2_enabled: bool = False
    #: Live demo already spoke quick greet + kickoff — skip INTRODUCING narration.
    live_opening_done: bool = False
    #: Injected Tier 2 proposer for tests / live reasoner. Returns dict|None.
    tier2_propose: Callable[..., object] | None = None
    #: Injected guardrail classify. Defaults to explore.guardrail.classify_action.
    tier2_classify: Callable[..., object] | None = None
    #: When True, only demo_playlist flows may run — no detours or handoffs.
    playlist_only: bool = False
    #: Walkthrough: skip STT wait and advance steps immediately (playlist demos).
    auto_advance_walkthrough: bool = False
    #: Guided playlist: no tier2/turn-brain detours; pause on step failure.
    strict_playlist: bool = False
    #: Set at auth boundary — controls hard-stop vs continue on click failures.
    demo_origin: Literal["dashboard_test", "public_embed"] = "dashboard_test"
    #: Bidirectional Gemini Live session (navigator.voice.live_agent.LiveAgent).
    #: When set, SPEAKING talks through it and the prospect can interrupt.
    live_agent: object | None = None
    #: Agent runtime orchestrator (navigator.agent_runtime.orchestrator.AgentOrchestrator).
    orchestrator: object | None = None
    #: Async pre-action narration; SPEAKING queues it, EXECUTING starts it.
    pre_action_speech: object | None = None
    #: Structured demo diagnostics sink. None means JSON logs to stdout.
    trace: Callable[[dict[str, object]], None] | None = None


def append_only(existing: list, new: list) -> list:
    """LangGraph reducer: nodes contribute to these lists, never replace them."""
    return [*existing, *new]


class _Clear(list):
    """Identity-stable empty. A fresh ``[]`` means 'append nothing', not clear."""


#: Sentinel a node returns to empty a queue that otherwise only accumulates.
CLEAR: list = _Clear()


def queue(existing: list, new: list) -> list:
    """Reducer for a work queue: append, or clear on the CLEAR sentinel.

    Needed because more than one node upstream of SPEAKING queues narration --
    without this, whichever ran last would silently drop the others' lines.
    Duplicate utterance ids are dropped so a regenerated translation cannot
    enqueue beside the original logical line.
    """
    if new is CLEAR or isinstance(new, _Clear):
        return []
    from navigator.agent.utterance import merge_narration

    return merge_narration(existing or [], new or [])


class CallState(TypedDict, total=False):
    session_id: UUID
    page_id: str
    """Where in the site graph the agent currently is."""
    plan: Plan | None
    pending_calls: list[ToolCall]
    """Plan steps not yet executed. EXECUTING pops one from the front per pass."""
    last_call: ToolCall | None
    """The call EXECUTING just ran, so VERIFYING knows what to check."""
    last_result: ToolResult | None
    last_page_id: str
    """page_id the call ran *on*, which differs from page_id after a navigate."""
    transcript: Annotated[list[str], append_only]
    """Everything said, by anyone, in order. Archived by ENDING."""
    narration: Annotated[list, queue]
    """What SPEAKING should say next. Accumulates across nodes, cleared once spoken.

    Items are strings or ``{"id", "text"}``. Same id = same logical utterance.
    """
    spoken_utterance_ids: Annotated[list[str], append_only]
    """Utterance ids SPEAKING already consumed this call. Re-entry must not replay."""
    entries: Annotated[list[ActionLogEntry], append_only]
    """This call's action log, in memory as well as in SQLite."""
    failures: Annotated[list[ActionLogEntry], append_only]
    """Subset of entries that failed. The REFLECTING input."""
    turns: int
    """Completed listen->speak cycles, so the graph can bound a scripted run."""
    max_turns: int
    finished: bool
    #: LISTENING detected a user correction of the last action.
    user_correction: bool
    phase: str
    walkthrough_flow_id: str
    #: Page that owns the walkthrough / interrupt flow catalog (stable across navigates).
    walkthrough_page_id: str
    walkthrough_step: int
    silence_rounds: int
    #: Sidebar label for cursor click (hybrid nav); EXECUTING consumes.
    nav_click_label: str | None
    #: Continue to the next demo_playlist flow when the current one ends.
    auto_play: bool
    #: Set when a detour is running; the default flow resumes at this step after.
    resume_step: int | None
    #: Page the default flow was on when it was paused.
    resume_page_id: str
    #: Flow the pending clarifying question would run on a yes.
    awaiting_confirm_flow_id: str | None
    #: Step-by-step detour flow answering a prospect question mid-demo.
    detour_flow_id: str
    detour_page_id: str
    detour_step: int
    #: Single-action detour (tier-2 / turn-brain); awaiting_resume after it runs.
    detour_one_shot: bool
    #: Step currently executing (strict playlist — advance only after verify).
    executing_step: int
    #: Next walkthrough index after the current step succeeds.
    planned_next_step: int
    #: Knowledge answer spoken; next turn asks if the question is answered.
    resume_checkin_pending: bool
    pre_action_speech: object | None


def initial_state(
    session_id: UUID,
    page_id: str,
    max_turns: int = 1,
    walkthrough_flow_id: str = "",
    *,
    auto_play: bool = True,
) -> CallState:
    return CallState(
        session_id=session_id,
        page_id=page_id,
        plan=None,
        pending_calls=[],
        last_call=None,
        last_result=None,
        last_page_id=page_id,
        transcript=[],
        narration=[],
        spoken_utterance_ids=[],
        entries=[],
        failures=[],
        turns=0,
        max_turns=max_turns,
        finished=False,
        user_correction=False,
        phase="walkthrough",
        walkthrough_flow_id=walkthrough_flow_id,
        walkthrough_page_id=page_id,
        walkthrough_step=0,
        silence_rounds=0,
        auto_play=auto_play,
        resume_step=None,
        resume_page_id="",
        awaiting_confirm_flow_id=None,
        detour_flow_id="",
        detour_page_id="",
        detour_step=0,
        detour_one_shot=False,
        resume_checkin_pending=False,
    )
