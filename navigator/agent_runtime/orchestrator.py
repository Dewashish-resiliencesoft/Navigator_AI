"""Central runtime: routing, state, execution lock, events.

Concurrency rules
─────────────────
* ONE worker thread executes at a time — enforced by ``_exec_lock``.
* A new user request while the worker is busy sets ``_pending_goal`` and
  sets ``_interrupt_flag``.  The running worker checks ``_interrupt_flag``
  after each atomic action; when set it stops the current plan and promotes
  the pending goal.
* ``interrupt()`` and ``handle_utterance()`` NEVER spawn a second execution
  thread.  The single worker loop is responsible for picking up new goals.
* This prevents the old recursive ``_run_task → interrupt → _run_task`` bug.

Speech rules
────────────
* ``live.acknowledge(hint)``   → immediate "I'm on it" ack before Flash starts.
* ``live.speak_result(text)``  → deliver the final task result/outcome.
* ``live.speak_error(text)``   → recoverable or unrecoverable failure speech.
* ``_finish_speech()`` is the internal helper that selects the right path.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable
from uuid import UUID

from navigator.agent_runtime.adapters.live_adapter import LiveAdapter
from navigator.agent_runtime.dom.builder import build_dom_state
from navigator.agent_runtime.events.bus import EventBus
from navigator.agent_runtime.execution.cancellation import (
    after_atomic_action,
    apply_interruption,
    should_cancel_remaining_plan,
)
from navigator.agent_runtime.execution.executor import execute_action
from navigator.agent_runtime.models import (
    ActionStatus,
    AgentEvent,
    AgentEventKind,
    AgentMode,
    AgentPlan,
    AgentSession,
    AgentTask,
    AgentWorldState,
    BrowserSlice,
    ConversationSlice,
    ExecutionSlice,
    TaskStatus,
    utc_now,
)
from navigator.agent_runtime import watchdog as _wd
from navigator.agent_runtime.planning.flash_planner import FlashPlanner
from navigator.agent_runtime.planning.groq_worker import GroqEventWorker
from navigator.agent_runtime.planning.router import (
    ROUTE_BACKCHANNEL,
    ROUTE_ANSWER,
    ROUTE_TASK_HANDOFF,
    RouteDecision,
    classify_utterance,
)
from navigator.agent_runtime.verification.verifier import (
    action_result_from_parts,
    build_verification,
)
from navigator.agent_runtime.world_state.store import WorldStateStore
from navigator.core.schemas import ActionLogEntry
from navigator.knowledge.site_graph import SiteGraph
from navigator.logs.store import ActionLog


class AgentOrchestrator:
    """Source of truth for interactive agent runtime.

    All realtime browser tasks go through this class.  LangGraph scripted
    playback is a separate, compatible path that does not share the exec lock.
    """

    def __init__(
        self,
        *,
        session: AgentSession,
        graph: SiteGraph,
        page: Any,
        log: ActionLog,
        page_id: str,
        live_agent: Any | None = None,
        brain_config: Any | None = None,
        on_frame: Callable[[], None] | None = None,
        speak: Callable[[str], None] | None = None,
    ) -> None:
        initial = AgentWorldState(
            session=session,
            browser=BrowserSlice(page_id=page_id),
        )
        self.store = WorldStateStore(initial)
        self.graph = graph
        self.page = page
        self.log = log
        self.on_frame = on_frame
        self.speak = speak
        self.live = LiveAdapter(live_agent)
        self.events = EventBus()
        reasoning_model = getattr(brain_config, "reasoning_model", "") or None
        self.planner = FlashPlanner(model=reasoning_model)
        self.groq_worker = GroqEventWorker()
        self.groq_worker.start()
        self.events.subscribe(self.groq_worker.enqueue)

        # Single execution lock — only one plan runs at a time.
        self._exec_lock = threading.Lock()
        # Set by handle_utterance/interrupt to ask the worker to yield after
        # the current atomic action.  Cleared when the worker picks it up.
        self._interrupt_flag = threading.Event()
        # Next goal to execute after the current atomic action finishes.
        # Protected by _goal_lock.
        self._pending_goal: str = ""
        self._goal_lock = threading.Lock()

        self._emit(AgentEventKind.SESSION_STARTED, payload={"product_id": session.product_id})

    # ── properties ────────────────────────────────────────────────────────

    @property
    def is_working(self) -> bool:
        """True while an execution thread holds the lock."""
        return self._exec_lock.locked()

    # ── public API ─────────────────────────────────────────────────────────

    def refresh_browser_state(self) -> None:
        dom = build_dom_state(self.page, page_id=self.store.state.browser.page_id, detailed=True)
        self.store.update(
            lambda s: s.model_copy(
                update={
                    "browser": s.browser.model_copy(
                        update={
                            "url": dom.get("url", ""),
                            "title": dom.get("title", ""),
                            "live_context": dom,
                            "semantic_elements": dom.get("elements", []),
                        }
                    )
                }
            )
        )
        self.live.push_world_state(
            page=self.store.state.browser.page_id,
            url=self.store.state.browser.url,
            task_status=self.store.state.task.status.value if self.store.state.task else "",
            task_goal=self.store.state.task.goal if self.store.state.task else "",
            browser_ready=True,
        )

    def handle_utterance(self, text: str) -> RouteDecision:
        """Route one heard user utterance.  Never blocks the caller."""
        decision = classify_utterance(text, agent_working=self.is_working)
        self._record_user_message(text, decision.route)

        if decision.route == ROUTE_BACKCHANNEL:
            # Rate-limit logic lives in BackchannelController (optional);
            # here we just do nothing — Live may backchannel naturally.
            pass

        elif decision.route == ROUTE_ANSWER:
            # Live handles it from its own context — no orchestrator needed.
            pass

        else:  # ROUTE_TASK_HANDOFF
            # 1. Emit immediate natural acknowledgement (wording by Live model).
            self.live.acknowledge(decision.ack_hint or text)
            self._emit(AgentEventKind.AGENT_ACKNOWLEDGED, payload={"text": text})

            # 2. Either start a new worker or replace the pending goal.
            self._request_task(text)

        return decision

    def interrupt(self, *, reason: str, new_goal: str) -> None:
        """Signal the worker to stop after the current atomic action and run new_goal.

        Does NOT spawn a new thread. The running worker loop picks up the goal.
        If no worker is running, starts one.
        """
        self.store.update(lambda s: apply_interruption(s, reason=reason, new_goal=new_goal))
        self._emit(
            AgentEventKind.USER_INTERRUPTED,
            payload={"reason": reason, "new_goal": new_goal},
        )
        if new_goal.strip():
            self.live.acknowledge(new_goal)
            self._request_task(new_goal)

    # ── internal orchestration ─────────────────────────────────────────────

    def _request_task(self, goal: str) -> None:
        """Queue or start a task — never recursive, never creates unbounded threads.

        If the worker is busy: set the pending goal and raise the interrupt
        flag so the worker yields after its current atomic action.

        If the worker is idle: start it directly.

        In both cases at most ONE worker thread exists at any time.
        """
        with self._goal_lock:
            self._pending_goal = goal

        if self.is_working:
            # Signal the running worker to stop after its current atomic action.
            self._interrupt_flag.set()
        else:
            # No worker running — start one.
            threading.Thread(
                target=self._worker_loop,
                name="agent-runtime-worker",
                daemon=True,
            ).start()

    def _worker_loop(self) -> None:
        """Single worker that runs goals until there are no more pending.

        Acquires the exec lock once; does NOT release and re-acquire between
        goals — this is safe because _request_task() checks is_working before
        starting a new thread.
        """
        if not self._exec_lock.acquire(blocking=False):
            # Another thread beat us to it (tiny race window); it will pick
            # up the pending goal itself via _interrupt_flag.
            return
        try:
            while True:
                with self._goal_lock:
                    goal = self._pending_goal
                    self._pending_goal = ""
                self._interrupt_flag.clear()

                if not goal:
                    break

                self._execute_task(goal)

                # After execution, check if a new goal arrived during the run.
                with self._goal_lock:
                    if not self._pending_goal:
                        break
                    # Loop continues and picks up the new goal.
        finally:
            self._exec_lock.release()

    def _execute_task(self, goal: str) -> None:
        task = AgentTask(goal=goal, status=TaskStatus.planning)
        self.store.update(
            lambda s: s.model_copy(
                update={
                    "task": task,
                    "agent": s.agent.model_copy(
                        update={"mode": AgentMode.thinking, "thinking": True}
                    ),
                }
            )
        )
        self._emit(AgentEventKind.TASK_CREATED, task_id=task.task_id, payload={"goal": goal})
        self.refresh_browser_state()

        plan = self.planner.plan(task_id=task.task_id, goal=goal, world=self.store.state)
        if plan is None or not plan.steps:
            self._speak_result("I couldn't figure out the next step on this page.")
            self._mark_task(TaskStatus.failed)
            return

        self.store.update(
            lambda s: s.model_copy(
                update={
                    "task": s.task.model_copy(update={"plan": plan, "status": TaskStatus.executing})
                    if s.task
                    else s.task,
                    "agent": s.agent.model_copy(
                        update={"thinking": False, "executing": True, "mode": AgentMode.executing}
                    ),
                }
            )
        )
        self._emit(AgentEventKind.PLAN_CREATED, task_id=task.task_id, payload={"steps": len(plan.steps)})
        self._execute_plan(plan)

    def _execute_plan(self, plan: AgentPlan) -> None:
        page_id = self.store.state.browser.page_id
        for idx, step in enumerate(plan.steps):
            # Check whether an interruption arrived before starting this step.
            if self._interrupt_flag.is_set() or should_cancel_remaining_plan(self.store.state):
                self._emit(AgentEventKind.TASK_CANCELLED, task_id=plan.task_id)
                self._mark_task(TaskStatus.failed)
                return

            if step.spoken:
                # Step narration — use result-speech path, not acknowledge.
                self._speak_result(step.spoken)

            self.store.update(
                lambda s, st=step: s.model_copy(
                    update={
                        "execution": ExecutionSlice(
                            action_id=st.action_id,
                            action_status=ActionStatus.running,
                            lock_holder="orchestrator",
                            flow_id=getattr(st, "flow_id", ""),
                            step_id=getattr(st, "step_id", ""),
                        ),
                        "watchdog": _wd.tick(s.watchdog),
                    }
                )
            )
            self._emit(AgentEventKind.ACTION_STARTED, task_id=plan.task_id, action_id=step.action_id)

            started = time.perf_counter()
            call, result, next_page_id, verify = execute_action(
                graph=self.graph,
                page=self.page,
                page_id=page_id,
                action=step,
                on_frame=self.on_frame,
            )
            latency = int((time.perf_counter() - started) * 1000)

            if call is not None and result is not None:
                entry = ActionLogEntry(
                    session_id=self.store.state.session.session_id,
                    product_id=self.store.state.session.product_id,
                    page=page_id,
                    tool_call=call,
                    expected_postcondition=call.expects,
                    actual_result=result,
                    verify=verify,
                    timestamp=utc_now(),
                )
                self.log.append(entry)

            page_id = next_page_id
            self.store.update(
                lambda s, np=next_page_id: s.model_copy(
                    update={"browser": s.browser.model_copy(update={"page_id": np})}
                )
            )
            self.refresh_browser_state()

            verification = build_verification(step, verify)
            self._emit(
                AgentEventKind.ACTION_COMPLETED,
                task_id=plan.task_id,
                action_id=step.action_id,
                latency_ms=latency,
                payload=action_result_from_parts(step, call, result, page_id).model_dump(mode="json"),
            )

            if verification.passed:
                self._emit(AgentEventKind.VERIFICATION_PASSED, task_id=plan.task_id, action_id=step.action_id)
                self.store.update(
                    lambda s: s.model_copy(update={"watchdog": _wd.clear_failure(s.watchdog)})
                )
                fp = _wd.state_fingerprint(
                    self.store.state.browser.url,
                    self.store.state.browser.semantic_elements,
                )
                self.store.update(
                    lambda s, f=fp: s.model_copy(update={"watchdog": _wd.record_state(s.watchdog, f)})
                )
            else:
                self._emit(AgentEventKind.VERIFICATION_FAILED, task_id=plan.task_id, action_id=step.action_id)
                self.store.update(
                    lambda s: s.model_copy(update={"watchdog": _wd.record_failure(s.watchdog)})
                )
                if plan.escalation == "screenshot":
                    self._emit(AgentEventKind.SCREENSHOT_CAPTURED, task_id=plan.task_id)
                if _wd.is_stuck(self.store.state.watchdog):
                    self._emit(AgentEventKind.LOOP_DETECTED, task_id=plan.task_id)
                self._speak_error("That step didn't verify — I'll try another approach.")
                self._mark_task(TaskStatus.failed)
                return

            self.store.update(after_atomic_action)
            self.store.update(
                lambda s, i=idx: s.model_copy(
                    update={
                        "task": s.task.model_copy(update={"current_step": i + 1})
                        if s.task
                        else s.task
                    }
                )
            )

            # After each atomic action: check for pending interruption.
            if self._interrupt_flag.is_set():
                self._emit(AgentEventKind.TASK_CANCELLED, task_id=plan.task_id)
                self._mark_task(TaskStatus.failed)
                return

        self._mark_task(TaskStatus.completed)
        self._emit(AgentEventKind.TASK_COMPLETED, task_id=plan.task_id)
        self._speak_result("Done — take a look at the screen.")

    # ── state helpers ──────────────────────────────────────────────────────

    def _mark_task(self, status: TaskStatus) -> None:
        self.store.update(
            lambda s: s.model_copy(
                update={
                    "task": s.task.model_copy(update={"status": status}) if s.task else s.task,
                    "agent": s.agent.model_copy(
                        update={
                            "executing": False,
                            "thinking": False,
                            "mode": AgentMode.listening,
                        }
                    ),
                    "execution": ExecutionSlice(action_status=ActionStatus.idle),
                }
            )
        )

    def _record_user_message(self, text: str, route: str) -> None:
        self.store.update(
            lambda s: s.model_copy(
                update={
                    "conversation": s.conversation.model_copy(
                        update={"last_user_message": text}
                    )
                }
            )
        )
        self._emit(AgentEventKind.USER_UTTERANCE, payload={"text": text, "route": route})

    # ── speech helpers — NEVER call acknowledge() for results ──────────────

    def _speak_result(self, text: str) -> None:
        """Deliver a task result or narration via Live (speak_result) or fallback."""
        self._update_speaking(True)
        self.store.update(
            lambda s: s.model_copy(
                update={
                    "conversation": s.conversation.model_copy(
                        update={"last_agent_message": text}
                    )
                }
            )
        )
        if self.live.is_available():
            self.live.speak_result(text)
        elif self.speak is not None:
            self.speak(text)
        self._update_speaking(False)

    def _speak_error(self, text: str) -> None:
        """Deliver a recovery/failure message to the visitor."""
        self._update_speaking(True)
        if self.live.is_available():
            self.live.speak_error(text)
        elif self.speak is not None:
            self.speak(text)
        self._update_speaking(False)

    def _update_speaking(self, speaking: bool) -> None:
        mode = AgentMode.speaking if speaking else AgentMode.listening
        self.store.update(
            lambda s: s.model_copy(
                update={"agent": s.agent.model_copy(update={"speaking": speaking, "mode": mode})}
            )
        )

    def close(self) -> None:
        self._emit(AgentEventKind.SESSION_ENDED)
        self.groq_worker.stop()

    def _emit(
        self,
        kind: AgentEventKind,
        *,
        task_id: UUID | None = None,
        action_id: UUID | None = None,
        latency_ms: int | None = None,
        payload: dict | None = None,
        flow_id: str = "",
        step_id: str = "",
    ) -> None:
        state = self.store.state
        event = AgentEvent(
            event=kind,
            session_id=state.session.session_id,
            task_id=task_id,
            action_id=action_id,
            flow_id=flow_id or state.execution.flow_id,
            step_id=step_id or state.execution.step_id,
            world_state_version=self.store.version(),
            latency_ms=latency_ms,
            payload=payload or {},
        )
        self.events.emit(event)
