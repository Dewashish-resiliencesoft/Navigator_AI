"""Typed runtime contracts — models propose; orchestrator executes."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from navigator.core.schemas import Postcondition, ToolCall, ToolResult, VerifyResult


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


class ExecutionSlice(BaseModel):
    action_id: UUID | None = None
    action_status: ActionStatus = ActionStatus.idle
    verification_status: Literal["pending", "passed", "failed", "ambiguous"] = "pending"
    lock_holder: str = ""


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


class AgentEvent(BaseModel):
    event: AgentEventKind
    timestamp: datetime = Field(default_factory=utc_now)
    session_id: UUID
    task_id: UUID | None = None
    action_id: UUID | None = None
    world_state_version: int = 0
    source: str = "runtime"
    latency_ms: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
