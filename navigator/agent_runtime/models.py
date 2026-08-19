"""Typed runtime contracts — models propose; orchestrator executes."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from navigator.core.schemas import Postcondition, ToolCall, ToolResult, VerifyResult

# ---------------------------------------------------------------------------
# Phase-1 additions: correlation IDs and watchdog timestamps
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    pending = "pending"
    planning = "planning"
    executing = "executing"
    verifying = "verifying"
    recovering = "recovering"
    completed = "completed"
    cancelled = "cancelled"
    failed = "failed"


class ActionStatus(str, Enum):
    idle = "idle"
    running = "running"
    verifying = "verifying"
    passed = "passed"
    failed = "failed"
    cancelled = "cancelled"


class AgentMode(str, Enum):
    idle = "idle"
    listening = "listening"
    speaking = "speaking"
    thinking = "thinking"
    executing = "executing"


class SemanticTarget(BaseModel):
    """Browser target by semantic id or visible label — never raw CSS."""

    semantic_id: str = ""
    label: str = ""
    page_id: str = ""


class SemanticVerification(BaseModel):
    check: Literal[
        "url_contains",
        "visible",
        "text_contains",
        "hidden",
    ] = "url_contains"
    expected: str = ""
    selector: str | None = None


class AgentAction(BaseModel):
    """One orchestrated browser step before ToolCall resolution."""

    action_id: UUID = Field(default_factory=uuid4)
    tool: Literal["click", "type", "navigate", "scroll", "hover", "wait", "select"] = "click"
    target: SemanticTarget = Field(default_factory=SemanticTarget)
    value: str = ""
    reason: str = ""
    verification: SemanticVerification | None = None
    spoken: str | None = None
    non_interruptible: bool = False


class AgentPlan(BaseModel):
    task_id: UUID
    goal: str
    steps: list[AgentAction] = Field(default_factory=list)
    escalation: Literal["dom", "screenshot", "none"] = "none"


class AgentTask(BaseModel):
    task_id: UUID = Field(default_factory=uuid4)
    goal: str
    status: TaskStatus = TaskStatus.pending
    source: Literal["user", "recovery", "system"] = "user"
    priority: Literal["interactive", "background"] = "interactive"
    current_step: int = 0
    plan: AgentPlan | None = None


class ConversationSlice(BaseModel):
    last_user_message: str = ""
    last_agent_message: str = ""
    interruption_pending: bool = False


class BrowserSlice(BaseModel):
    url: str = ""
    title: str = ""
    page_id: str = ""
    dom_snapshot_ref: str = ""
    semantic_elements: list[dict[str, Any]] = Field(default_factory=list)
    live_context: dict[str, Any] = Field(default_factory=dict)
    screenshot_ref: str = ""
    focused_element: str = ""


class DemoStepStatus(str, Enum):
    """Phase-3 DemoStep lifecycle. Browser state is authoritative."""
    pending = "pending"
    narrating = "narrating"
    acting = "acting"
    verifying = "verifying"
    complete = "complete"
    failed = "failed"
    recovering = "recovering"


class SafetyClass(str, Enum):
    safe_demo = "safe_demo"
    user_input = "user_input"
    mutation = "mutation"
    destructive = "destructive"


class InteractionMode(str, Enum):
    none = "none"
    auto = "auto"
    ask = "ask"
    optional = "optional"
    confirm = "confirm"
    manual_handoff = "manual_handoff"


class RecoveryPolicy(str, Enum):
    replan = "replan"
    skip = "skip"
    handoff = "handoff"
    fail = "fail"


class SessionOutcome(str, Enum):
    success = "success"
    partial_success = "partial_success"
    recovering = "recovering"
    handoff_required = "handoff_required"
    failed = "failed"


# ---------------------------------------------------------------------------
# Phase-1: watchdog/loop-detector state
# ---------------------------------------------------------------------------

class WatchdogSlice(BaseModel):
    """Execution watchdog — detects freezes and action loops."""
    last_action_started_at: datetime | None = None
    action_timeout_ms: int = 30_000
    consecutive_failures: int = 0
    visited_states: list[str] = Field(default_factory=list)
    loop_detected: bool = False
    timed_out: bool = False


class ExecutionSlice(BaseModel):
    action_id: UUID | None = None
    action_status: ActionStatus = ActionStatus.idle
    verification_status: Literal["pending", "passed", "failed", "ambiguous"] = "pending"
    lock_holder: str = ""
    # Phase-1: per-action correlation tracing
    flow_id: str = ""
    step_id: str = ""
    attempt: int = 0


class AgentSlice(BaseModel):
    mode: AgentMode = AgentMode.idle
    speaking: bool = False
    listening: bool = True
    thinking: bool = False
    executing: bool = False


class InterruptionSlice(BaseModel):
    requested: bool = False
    reason: str = ""
    new_goal: str = ""
    policy: Literal["cancel_after_atomic_action", "immediate"] = "cancel_after_atomic_action"


class MemorySlice(BaseModel):
    relevant_context: str = ""
    previous_failures: list[str] = Field(default_factory=list)
    approved_corrections: list[str] = Field(default_factory=list)


class PendingSlice(BaseModel):
    action: AgentAction | None = None
    clarification: str = ""
    recovery: str = ""


class AgentSession(BaseModel):
    session_id: UUID
    product_id: str
    revision_id: int = 0
    origin: Literal["dashboard_test", "public_embed"] = "dashboard_test"


class AgentWorldState(BaseModel):
    """Single authoritative runtime state. Redis/logs mirror; they do not own truth."""

    version: int = 0
    updated_at: datetime = Field(default_factory=utc_now)
    session: AgentSession
    conversation: ConversationSlice = Field(default_factory=ConversationSlice)
    browser: BrowserSlice = Field(default_factory=BrowserSlice)
    task: AgentTask | None = None
    execution: ExecutionSlice = Field(default_factory=ExecutionSlice)
    agent: AgentSlice = Field(default_factory=AgentSlice)
    interruption: InterruptionSlice = Field(default_factory=InterruptionSlice)
    memory: MemorySlice = Field(default_factory=MemorySlice)
    pending: PendingSlice = Field(default_factory=PendingSlice)
    # Phase-1
    watchdog: WatchdogSlice = Field(default_factory=WatchdogSlice)
    # Phase-7: scoped demo session memory (cleared at session end)
    demo_session: "DemoSessionContext" = Field(default_factory=lambda: DemoSessionContext())
    # Phase-8: session outcome
    outcome: SessionOutcome = SessionOutcome.success


class ActionResult(BaseModel):
    action_id: UUID
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    page_id: str = ""


class VerificationResult(BaseModel):
    action_id: UUID
    passed: bool
    verify: VerifyResult | None = None
    postcondition: Postcondition | None = None


class InterruptionRequest(BaseModel):
    reason: str
    new_goal: str = ""
    policy: Literal["cancel_after_atomic_action", "immediate"] = "cancel_after_atomic_action"


class AgentEventKind(str, Enum):
    SESSION_STARTED = "SESSION_STARTED"
    USER_UTTERANCE = "USER_UTTERANCE"
    AGENT_ACKNOWLEDGED = "AGENT_ACKNOWLEDGED"
    TASK_CREATED = "TASK_CREATED"
    PLAN_CREATED = "PLAN_CREATED"
    ACTION_STARTED = "ACTION_STARTED"
    ACTION_COMPLETED = "ACTION_COMPLETED"
    VERIFICATION_PASSED = "VERIFICATION_PASSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    SCREENSHOT_CAPTURED = "SCREENSHOT_CAPTURED"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    RECOVERY_COMPLETED = "RECOVERY_COMPLETED"
    USER_INTERRUPTED = "USER_INTERRUPTED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_COMPLETED = "TASK_COMPLETED"
    SESSION_ENDED = "SESSION_ENDED"
    # Phase-1: watchdog / loop events
    ACTION_TIMED_OUT = "ACTION_TIMED_OUT"
    LOOP_DETECTED = "LOOP_DETECTED"
    STEP_COMPLETE = "STEP_COMPLETE"
    BROWSER_STATE_SETTLED = "BROWSER_STATE_SETTLED"
    # Phase-3: DemoStep lifecycle
    DEMO_STEP_STARTED = "DEMO_STEP_STARTED"
    DEMO_STEP_COMPLETE = "DEMO_STEP_COMPLETE"
    DEMO_STEP_FAILED = "DEMO_STEP_FAILED"
    # Phase-7: interaction events
    INTERACTION_REQUESTED = "INTERACTION_REQUESTED"
    INTERACTION_RESOLVED = "INTERACTION_RESOLVED"
    INTERACTION_TIMED_OUT = "INTERACTION_TIMED_OUT"
    # Phase-8: session outcome
    SESSION_SUCCESS = "SESSION_SUCCESS"
    SESSION_PARTIAL = "SESSION_PARTIAL"
    SESSION_HANDOFF = "SESSION_HANDOFF"
    SESSION_FAILED = "SESSION_FAILED"


class AgentEvent(BaseModel):
    event: AgentEventKind
    timestamp: datetime = Field(default_factory=utc_now)
    session_id: UUID
    task_id: UUID | None = None
    action_id: UUID | None = None
    # Phase-1 correlation IDs
    flow_id: str = ""
    step_id: str = ""
    world_state_version: int = 0
    source: str = "runtime"
    latency_ms: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Phase-7: DemoSessionContext — scoped per demo, cleared on session end
# ---------------------------------------------------------------------------

class DemoSessionContext(BaseModel):
    """Visitor-provided values collected during one demo. Never persisted."""
    visitor_name: str = ""
    company: str = ""
    role: str = ""
    phone: str = ""
    email: str = ""
    campaign_name: str = ""
    extra: dict[str, str] = Field(default_factory=dict)

    def get(self, key: str, default: str = "") -> str:
        if hasattr(self, key):
            val = getattr(self, key)
            return str(val) if val else default
        return self.extra.get(key, default)

    def set(self, key: str, value: str) -> None:
        if hasattr(self, key) and key != "extra":
            object.__setattr__(self, key, value)
        else:
            self.extra[key] = value


# ---------------------------------------------------------------------------
# Phase-3: DemoStep — the new semantic execution contract
# ---------------------------------------------------------------------------

class DemoStepAction(BaseModel):
    tool: Literal["click", "type", "navigate", "scroll", "hover", "wait", "select"] = "click"
    target: "SemanticTarget" = Field(default_factory=lambda: SemanticTarget())
    value: str = ""


class DemoStepNarration(BaseModel):
    default: str = ""
    source_transcript: str = ""
    semantic_intent: str = ""


class DemoStepVerification(BaseModel):
    """Real state verification — browser state is authoritative."""
    url_contains: str = ""
    visible: str = ""
    text_contains: str = ""
    dom_fingerprint: str = ""
    active_nav: str = ""
    settled: bool = True


class DemoStepInteraction(BaseModel):
    mode: InteractionMode = InteractionMode.none
    input_name: str = ""
    input_type: str = "text"
    prompt: str = ""
    fallback_after_ms: int = 8000
    fallback_value: str = ""


class DemoStepPresentation(BaseModel):
    highlight: str = ""
    pause_after_ms: int = 400
    cursor_path: list[dict[str, int]] = Field(default_factory=list)


class DemoStepRecovery(BaseModel):
    on_failure: RecoveryPolicy = RecoveryPolicy.replan
    max_retries: int = 2


class DemoStep(BaseModel):
    """Semantic execution contract for one step in a live demo.

    The browser state reaching ``verification`` is the only gate to advance.
    Timing in ``presentation`` is a hint only — it never controls execution.
    """
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    objective: str = ""
    action: DemoStepAction = Field(default_factory=DemoStepAction)
    narration: DemoStepNarration = Field(default_factory=DemoStepNarration)
    verification: DemoStepVerification = Field(default_factory=DemoStepVerification)
    interaction: DemoStepInteraction = Field(default_factory=DemoStepInteraction)
    safety: SafetyClass = SafetyClass.safe_demo
    presentation: DemoStepPresentation = Field(default_factory=DemoStepPresentation)
    recovery: DemoStepRecovery = Field(default_factory=DemoStepRecovery)
    status: DemoStepStatus = DemoStepStatus.pending
    needs_approval: bool = False
    approved: bool = False


# ---------------------------------------------------------------------------
# Phase-4: DemoGraph — "how to demo" alongside SiteGraph "what exists"
# ---------------------------------------------------------------------------

class DemoMode(str, Enum):
    automated = "automated"
    interactive = "interactive"
    guided = "guided"


class DemoFlow(BaseModel):
    flow_id: str
    objective: str = ""
    audience: str = ""
    priority: int = 1
    steps: list[DemoStep] = Field(default_factory=list)


class DemoPlaylist(BaseModel):
    mode: DemoMode = DemoMode.automated
    flows: list[str] = Field(default_factory=list)


class DemoGraph(BaseModel):
    """Semantic demo layer — answers 'how to demo'. Paired with SiteGraph."""
    version: int = 1
    product_id: str = ""
    flows: dict[str, DemoFlow] = Field(default_factory=dict)
    playlist: DemoPlaylist = Field(default_factory=DemoPlaylist)
    onboarding_text: str = ""
    completion_text: str = "That covers the walkthrough. What would you like to explore next?"
    failure_text: str = "I've run into an issue. Our team will follow up with you."
    handoff_text: str = "Let me connect you with our team who can continue from here."


# ---------------------------------------------------------------------------
# Phase-5: Product Discovery world model
# ---------------------------------------------------------------------------

class DiscoveryStage(str, Enum):
    explore = "explore"
    understand = "understand"
    compose = "compose"
    curate = "curate"
    done = "done"


class DiscoveredCapability(BaseModel):
    area_id: str
    label: str
    description: str = ""
    page_ids: list[str] = Field(default_factory=list)
    flow_candidates: list[str] = Field(default_factory=list)
    progress_score: float = 0.0
    safety: SafetyClass = SafetyClass.safe_demo


class ExplorationWorldModel(BaseModel):
    """Explorer's persistent world model across the discovery run."""
    product_id: str = ""
    stage: DiscoveryStage = DiscoveryStage.explore
    current_url: str = ""
    current_page_id: str = ""
    current_goal: str = ""
    visited_states: list[str] = Field(default_factory=list)
    visited_elements: list[str] = Field(default_factory=list)
    dead_ends: list[str] = Field(default_factory=list)
    risky_actions: list[str] = Field(default_factory=list)
    capabilities: list[DiscoveredCapability] = Field(default_factory=list)
    completed_flows: list[str] = Field(default_factory=list)
    failed_actions: list[str] = Field(default_factory=list)
    pending_questions: list[str] = Field(default_factory=list)
    branch_scores: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Phase-8: Structured error log entry (3-layer)
# ---------------------------------------------------------------------------

class ErrorLayer(str, Enum):
    developer = "developer"
    agent = "agent"
    visitor = "visitor"


class StructuredError(BaseModel):
    """Three-layer error: technical + agent-facing + visitor-facing."""
    session_id: UUID
    flow_id: str = ""
    step_id: str = ""
    action_id: UUID | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    component: str = ""
    module: str = ""
    model: str = ""
    provider: str = ""
    tool: str = ""
    browser_url: str = ""
    expected_state: str = ""
    actual_state: str = ""
    error_type: str = ""
    error_message: str = ""
    retry_count: int = 0
    recovery_action: str = ""
    final_result: str = ""
    # Three layers
    developer_message: str = ""
    agent_message: str = ""
    visitor_message: str = ""
