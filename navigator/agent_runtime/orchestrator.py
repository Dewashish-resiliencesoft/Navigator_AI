"""Central runtime: routing, state, execution lock, events."""

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
from navigator.agent_runtime.planning.flash_planner import FlashPlanner
from navigator.agent_runtime.planning.groq_worker import GroqEventWorker
from navigator.agent_runtime.planning.router import RouteDecision, classify_utterance
from navigator.agent_runtime.verification.verifier import (
    action_result_from_parts,
    build_verification,
)
from navigator.agent_runtime.world_state.store import WorldStateStore
from navigator.core.schemas import ActionLogEntry
from navigator.knowledge.site_graph import SiteGraph
from navigator.logs.store import ActionLog


class AgentOrchestrator:
    """Source of truth for interactive agent runtime."""

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
        self._exec_lock = threading.Lock()
        self._emit(AgentEventKind.SESSION_STARTED, payload={"product_id": session.product_id})

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
        self.live.push_dom_context(self.page, page_id=self.store.state.browser.page_id)

    def handle_utterance(self, text: str) -> RouteDecision:
        decision = classify_utterance(text)
        self.store.update(
            lambda s: s.model_copy(
                update={
                    "conversation": s.conversation.model_copy(
                        update={"last_user_message": text}
                    )
                }
            )
        )
        self._emit(AgentEventKind.USER_UTTERANCE, payload={"text": text, "route": decision.route})
        if decision.route == "orchestrator":
            ack = "Sure — let me check that for you."
            self.live.acknowledge(ack)
            self._emit(AgentEventKind.AGENT_ACKNOWLEDGED, payload={"text": ack})
            threading.Thread(
                target=self._run_task,
                args=(text,),
                name="agent-runtime-task",
                daemon=True,
            ).start()
        return decision

    def interrupt(self, *, reason: str, new_goal: str) -> None:
        self.store.update(lambda s: apply_interruption(s, reason=reason, new_goal=new_goal))
        self._emit(
            AgentEventKind.USER_INTERRUPTED,
            payload={"reason": reason, "new_goal": new_goal},
        )
        if new_goal.strip():
            self.live.acknowledge("Sure — switching focus.")
            threading.Thread(
                target=self._run_task,
                args=(new_goal,),
                name="agent-runtime-interrupt-task",
                daemon=True,
            ).start()

    def _run_task(self, goal: str) -> None:
        if not self._exec_lock.acquire(blocking=False):
            self.interrupt(reason="new_request", new_goal=goal)
            return
        try:
            self._execute_task(goal)
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
            msg = "I couldn't figure out the next step on this page."
            self._finish_speech(msg)
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
            if should_cancel_remaining_plan(self.store.state):
                self._emit(AgentEventKind.TASK_CANCELLED, task_id=plan.task_id)
                return

            if step.spoken:
                self._finish_speech(step.spoken)

            self.store.update(
                lambda s, st=step: s.model_copy(
                    update={
                        "execution": ExecutionSlice(
                            action_id=st.action_id,
                            action_status=ActionStatus.running,
                            lock_holder="orchestrator",
                        )
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
            else:
                self._emit(AgentEventKind.VERIFICATION_FAILED, task_id=plan.task_id, action_id=step.action_id)
                if plan.escalation == "screenshot":
                    self._emit(AgentEventKind.SCREENSHOT_CAPTURED, task_id=plan.task_id)
                self._finish_speech("That step didn't verify — I'll try another approach.")
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

        self._mark_task(TaskStatus.completed)
        self._emit(AgentEventKind.TASK_COMPLETED, task_id=plan.task_id)
        self._finish_speech("Done — take a look at the screen.")

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

    def _finish_speech(self, text: str) -> None:
        self.store.update(
            lambda s: s.model_copy(
                update={
                    "conversation": s.conversation.model_copy(
                        update={"last_agent_message": text}
                    ),
                    "agent": s.agent.model_copy(
                        update={"speaking": True, "mode": AgentMode.speaking}
                    ),
                }
            )
        )
        if self.live.is_available():
            self.live.acknowledge(text)
        elif self.speak is not None:
            self.speak(text)
        self.store.update(
            lambda s: s.model_copy(
                update={
                    "agent": s.agent.model_copy(
                        update={"speaking": False, "mode": AgentMode.listening}
                    )
                }
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
    ) -> None:
        event = AgentEvent(
            event=kind,
            session_id=self.store.state.session.session_id,
            task_id=task_id,
            action_id=action_id,
            world_state_version=self.store.version(),
            latency_ms=latency_ms,
            payload=payload or {},
        )
        self.events.emit(event)
