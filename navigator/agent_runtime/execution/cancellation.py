"""Atomic-action interruption semantics."""

from __future__ import annotations

from navigator.agent_runtime.models import (
    ActionStatus,
    AgentWorldState,
    InterruptionSlice,
    TaskStatus,
)


def apply_interruption(state: AgentWorldState, *, reason: str, new_goal: str) -> AgentWorldState:
    state.interruption = InterruptionSlice(
        requested=True,
        reason=reason,
        new_goal=new_goal,
        policy="cancel_after_atomic_action",
    )
    return state


def after_atomic_action(state: AgentWorldState) -> AgentWorldState:
    """Finish current action, then cancel task if interruption pending."""
    if not state.interruption.requested:
        return state
    if state.execution.action_status == ActionStatus.running:
        return state
    if state.task is not None:
        state.task.status = TaskStatus.cancelled
    state.interruption.requested = False
    state.pending.recovery = ""
    return state


def should_cancel_remaining_plan(state: AgentWorldState) -> bool:
    return state.task is not None and state.task.status == TaskStatus.cancelled
