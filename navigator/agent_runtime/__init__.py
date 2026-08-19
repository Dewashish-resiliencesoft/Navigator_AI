"""Interactive agent runtime: Orchestrator + World State + model adapters.

Gemini Live owns realtime audio. Flash owns deep planning. Groq enriches events
asynchronously. Playwright executes semantic browser actions through one lock.

Phase architecture (all 9):
  1  Watchdog + correlation IDs
  2  Settled state + real verification
  3  DemoStep — semantic execution contract
  4  DemoGraph — 'how to demo' layer alongside SiteGraph
  5  Product Discovery Agent — Explore→Understand→Compose→Curate
  6  DemoStepExecutor — browser-state-authoritative live runner
  7  Interaction Engine — AUTO/ASK/OPTIONAL/CONFIRM/HANDOFF + session memory
  8  Failure lifecycle — deterministic outcomes + 3-layer error log
  9  Legacy timeline demoted to presentation hints only
"""

from navigator.agent_runtime.models import (
    AgentAction,
    AgentEvent,
    AgentPlan,
    AgentSession,
    AgentTask,
    AgentWorldState,
    DemoGraph,
    DemoMode,
    DemoSessionContext,
    DemoStep,
    DemoStepStatus,
    ExplorationWorldModel,
    InteractionMode,
    InterruptionRequest,
    RecoveryPolicy,
    SafetyClass,
    SessionOutcome,
    StructuredError,
    VerificationResult,
)
from navigator.agent_runtime.orchestrator import AgentOrchestrator

__all__ = [
    "AgentAction",
    "AgentEvent",
    "AgentOrchestrator",
    "AgentPlan",
    "AgentSession",
    "AgentTask",
    "AgentWorldState",
    "DemoGraph",
    "DemoMode",
    "DemoSessionContext",
    "DemoStep",
    "DemoStepStatus",
    "ExplorationWorldModel",
    "InteractionMode",
    "InterruptionRequest",
    "RecoveryPolicy",
    "SafetyClass",
    "SessionOutcome",
    "StructuredError",
    "VerificationResult",
]
