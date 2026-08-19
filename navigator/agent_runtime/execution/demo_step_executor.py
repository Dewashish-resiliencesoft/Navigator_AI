"""Phase-6: DemoStep executor — browser state is authoritative.

Invariant: no DemoStep can advance until its required browser state transition
has been verified. This is enforced by code, never by the LLM or timing.

Flow for each DemoStep:
  1. Prepare narration (emit DEMO_STEP_STARTED)
  2. Capture DOM fingerprint before action
  3. Execute action (one atomic Playwright call)
  4. Wait for settled state
  5. Verify (url / dom / visible / active-nav)
  6. If passed → emit STEP_COMPLETE → advance
  7. If failed → recovery ladder (retry → replan → skip → handoff)

Speech and browser are synchronised by the step boundary, not by a clock.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from playwright.sync_api import Error as PlaywrightError, Page

from navigator.agent_runtime.models import (
    AgentEventKind,
    AgentWorldState,
    DemoStep,
    DemoStepStatus,
    ExecutionSlice,
    RecoveryPolicy,
    SafetyClass,
    SessionOutcome,
    StructuredError,
    WatchdogSlice,
    utc_now,
)
from navigator.agent_runtime import watchdog as wd
from navigator.agent_runtime.verification.settled import verify_step, wait_settled
from navigator.agent_runtime.execution.executor import execute_action
from navigator.knowledge.site_graph import SiteGraph


_ACTION_TIMEOUT_MS = 30_000
_MAX_RETRIES = 2


def _dom_fingerprint_before(page: Page) -> str:
    from navigator.agent_runtime.verification.settled import _dom_fingerprint
    return _dom_fingerprint(page)


def execute_demo_step(
    step: DemoStep,
    *,
    world: AgentWorldState,
    graph: SiteGraph,
    page: Page,
    emit: Callable,
    speak: Callable[[str], None] | None = None,
    on_frame: Callable[[], None] | None = None,
) -> tuple[DemoStepStatus, AgentWorldState, StructuredError | None]:
    """Execute one DemoStep. Returns (status, updated_world, error_or_None)."""

    flow_id = world.execution.flow_id
    step_id = step.id

    # 1. Narrate (speak before acting)
    narration = step.narration.default or step.narration.source_transcript
    if narration and speak:
        speak(narration)
    emit(AgentEventKind.DEMO_STEP_STARTED, flow_id=flow_id, step_id=step_id,
         payload={"objective": step.objective, "safety": step.safety.value})

    # Safety gate — mutating steps that aren't approved cannot run
    if step.safety in (SafetyClass.mutation, SafetyClass.destructive) and not step.approved:
        err = _make_error(world, step, "mutation_not_approved",
                          "Step requires approval before executing in a live demo.")
        emit(AgentEventKind.DEMO_STEP_FAILED, flow_id=flow_id, step_id=step_id,
             payload={"reason": "not_approved"})
        return DemoStepStatus.failed, world, err

    # 2. Capture pre-action DOM fingerprint
    pre_fp = _dom_fingerprint_before(page)

    # 3. Execute with watchdog
    new_watchdog = wd.tick(world.watchdog)
    world = world.model_copy(update={"watchdog": new_watchdog})

    attempt = 0
    last_error: StructuredError | None = None

    while attempt <= _MAX_RETRIES:
        attempt += 1
        started = time.perf_counter()

        # Convert DemoStep action to AgentAction and execute
        from navigator.agent_runtime.models import AgentAction, SemanticTarget, SemanticVerification
        agent_action = AgentAction(
            tool=step.action.tool,
            target=SemanticTarget(
                semantic_id=step.action.target.semantic_id,
                label=step.action.target.semantic_id.replace("_", " "),
                page_id=step.action.target.page_id,
            ),
            value=step.action.value,
            reason=step.objective,
            non_interruptible=True,
        )

        try:
            call, result, next_page_id, verify = execute_action(
                graph=graph,
                page=page,
                page_id=world.browser.page_id,
                action=agent_action,
                on_frame=on_frame,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - started) * 1000)
            last_error = _make_error(world, step, type(exc).__name__, str(exc))
            new_watchdog = wd.record_failure(world.watchdog)
            world = world.model_copy(update={"watchdog": new_watchdog})
            if attempt > _MAX_RETRIES:
                break
            continue

        elapsed = int((time.perf_counter() - started) * 1000)

        # Watchdog: check timeout
        if wd.check_timeout(world.watchdog):
            emit(AgentEventKind.ACTION_TIMED_OUT, flow_id=flow_id, step_id=step_id)
            last_error = _make_error(world, step, "ActionTimeout",
                                     f"Action exceeded {_ACTION_TIMEOUT_MS}ms")
            break

        # 4. Wait for settled state
        settled = wait_settled(page)
        if settled:
            emit(AgentEventKind.BROWSER_STATE_SETTLED, flow_id=flow_id, step_id=step_id)

        # 5. Verify — dom_changed baseline
        passed, reason = verify_step(page, step.verification, before_fingerprint=pre_fp)

        if passed:
            # Clear watchdog failure count
            new_watchdog = wd.clear_failure(world.watchdog)
            # Track visited state for loop detection
            new_watchdog = wd.record_state(new_watchdog, wd.state_fingerprint(page.url, []))
            world = world.model_copy(update={
                "watchdog": new_watchdog,
                "browser": world.browser.model_copy(update={
                    "url": page.url,
                    "page_id": next_page_id,
                }),
            })
            emit(AgentEventKind.VERIFICATION_PASSED, flow_id=flow_id, step_id=step_id,
                 payload={"reason": reason, "attempts": attempt})
            emit(AgentEventKind.DEMO_STEP_COMPLETE, flow_id=flow_id, step_id=step_id,
                 payload={"latency_ms": elapsed})
            return DemoStepStatus.complete, world, None

        # Verification failed
        new_watchdog = wd.record_failure(world.watchdog)
        world = world.model_copy(update={"watchdog": new_watchdog})
        last_error = _make_error(world, step, "VerificationFailed",
                                 f"Verification failed after action: {reason}")
        emit(AgentEventKind.VERIFICATION_FAILED, flow_id=flow_id, step_id=step_id,
             payload={"reason": reason, "attempt": attempt})

        if wd.is_stuck(world.watchdog):
            emit(AgentEventKind.LOOP_DETECTED, flow_id=flow_id, step_id=step_id)
            break

    # Recovery
    emit(AgentEventKind.DEMO_STEP_FAILED, flow_id=flow_id, step_id=step_id)
    return _apply_recovery(step, world, last_error)


def _apply_recovery(
    step: DemoStep,
    world: AgentWorldState,
    error: StructuredError | None,
) -> tuple[DemoStepStatus, AgentWorldState, StructuredError | None]:
    policy = step.recovery.on_failure
    if policy == RecoveryPolicy.skip:
        return DemoStepStatus.failed, world, error
    if policy == RecoveryPolicy.replan:
        return DemoStepStatus.recovering, world, error
    if policy == RecoveryPolicy.handoff:
        world = world.model_copy(update={"outcome": SessionOutcome.handoff_required})
        return DemoStepStatus.failed, world, error
    return DemoStepStatus.failed, world, error


def _make_error(
    world: AgentWorldState,
    step: DemoStep,
    error_type: str,
    message: str,
) -> StructuredError:
    from navigator.agent_runtime.models import StructuredError
    return StructuredError(
        session_id=world.session.session_id,
        flow_id=world.execution.flow_id,
        step_id=step.id,
        timestamp=utc_now(),
        component="demo_step_executor",
        module="navigator.agent_runtime.execution.demo_step_executor",
        browser_url=world.browser.url,
        expected_state=str(step.verification.model_dump()),
        actual_state=world.browser.url,
        error_type=error_type,
        error_message=message,
        retry_count=step.recovery.max_retries,
        # Three layers
        developer_message=f"{error_type}: {message} [step={step.id}, url={world.browser.url}]",
        agent_message="I ran into an issue with this step — let me try another approach.",
        visitor_message=(
            "It looks like this part of the demo isn't responding correctly. "
            "I'll stop here and have our team follow up with you."
            if world.outcome == SessionOutcome.handoff_required
            else "I ran into a small issue here. Let me try that again."
        ),
    )
