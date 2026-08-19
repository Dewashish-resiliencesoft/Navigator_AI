"""Phase-8: Failure lifecycle — deterministic session outcomes.

Session outcomes are not decided by the LLM. The orchestrator uses this module
to pick the correct outcome state and the correct visitor-facing script.

Outcome ladder (in priority order):
  SUCCESS          — all planned flows completed
  PARTIAL_SUCCESS  — most flows completed, one or two failed
  RECOVERING       — currently in recovery, outcome pending
  HANDOFF_REQUIRED — a step required human handoff
  FAILED           — session could not complete any meaningful flow

Visitor scripts are deterministic templates. The LLM may personalise tone
(name, context) but cannot deviate from the template or skip the handoff step.
"""

from __future__ import annotations

from navigator.agent_runtime.models import (
    AgentEventKind,
    DemoGraph,
    SessionOutcome,
    StructuredError,
)


# Visitor-facing scripts (templates, not LLM-generated)
_SCRIPTS: dict[SessionOutcome, str] = {
    SessionOutcome.success: (
        "That covers the walkthrough. {visitor_name}Thank you for joining me today. "
        "What questions do you have, or is there anything you'd like to see again?"
    ),
    SessionOutcome.partial_success: (
        "We covered the main highlights of the product. "
        "I wasn't able to show you everything today, but our team can walk you through the rest. "
        "What questions do you have so far?"
    ),
    SessionOutcome.handoff_required: (
        "I've run into an issue that requires a bit more attention. "
        "Rather than give you an incomplete experience, I'll stop here. "
        "Our team will follow up and provide the full walkthrough — "
        "you'll have someone reach out shortly."
    ),
    SessionOutcome.failed: (
        "I'm sorry — I've run into an issue while demonstrating this part of the product. "
        "I don't want to give you an incomplete experience, so I'll stop here. "
        "Our team will follow up with you directly."
    ),
    SessionOutcome.recovering: (
        "I ran into a small issue here. Let me try that again."
    ),
}


def visitor_script(
    outcome: SessionOutcome,
    *,
    visitor_name: str = "",
) -> str:
    template = _SCRIPTS.get(outcome, _SCRIPTS[SessionOutcome.failed])
    prefix = f"{visitor_name}, " if visitor_name else ""
    return template.format(visitor_name=prefix)


def determine_outcome(
    *,
    completed_flows: list[str],
    failed_flows: list[str],
    demo_graph: DemoGraph | None,
    handoff_requested: bool,
) -> SessionOutcome:
    """Deterministic outcome based on flow completion, not LLM judgment."""
    if handoff_requested:
        return SessionOutcome.handoff_required

    total = len(completed_flows) + len(failed_flows)
    if total == 0:
        return SessionOutcome.failed

    ratio = len(completed_flows) / total if total > 0 else 0.0

    if ratio == 1.0:
        return SessionOutcome.success
    if ratio >= 0.5:
        return SessionOutcome.partial_success
    return SessionOutcome.failed


def format_error_log(error: StructuredError) -> str:
    """Developer-readable structured error (not shown to visitor)."""
    lines = [
        "ERROR",
        "─" * 40,
        f"Flow:    {error.flow_id}",
        f"Step:    {error.step_id}",
        f"Action:  {error.tool}",
        "",
        f"Module:  {error.module}",
        f"Provider:{error.provider or 'Playwright'}",
        f"Model:   {error.model or 'none'}",
        "",
        f"Error:   {error.error_type}",
        f"Message: {error.error_message}",
        f"URL:     {error.browser_url}",
        "",
        f"Expected:{error.expected_state}",
        f"Actual:  {error.actual_state}",
        "",
        f"Retries: {error.retry_count}",
        f"Recovery:{error.recovery_action}",
        f"Result:  {error.final_result or 'FAILED'}",
    ]
    return "\n".join(lines)
