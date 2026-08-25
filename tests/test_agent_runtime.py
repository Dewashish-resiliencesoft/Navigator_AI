"""Unit tests for agent runtime contracts and routing."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch
from uuid import uuid4

from navigator.agent_runtime.dom.builder import semantic_id_for
from navigator.agent_runtime.models import (
    AgentAction,
    AgentPlan,
    AgentSession,
    AgentWorldState,
    DemoFlow,
    DemoGraph,
    DemoPlaylist,
    DemoStep,
    DemoStepAction,
    DemoStepInteraction,
    DemoStepRecovery,
    DemoStepVerification,
    InteractionMode,
    RecoveryPolicy,
    SemanticTarget,
    ToolResult,
    VerificationResult,
    TaskStatus,
)
from navigator.agent_runtime.planning.router import (
    ROUTE_BACKCHANNEL,
    ROUTE_ANSWER,
    ROUTE_TASK_HANDOFF,
    classify_utterance,
)


# ── Router ─────────────────────────────────────────────────────────────────


def test_router_empty_is_backchannel():
    assert classify_utterance("").route == ROUTE_BACKCHANNEL


def test_router_filler_ack():
    for filler in ("okay", "yes", "yeah", "mm hmm", "right", "got it", "sure"):
        d = classify_utterance(filler)
        assert d.route == ROUTE_BACKCHANNEL, f"Expected backchannel for {filler!r}, got {d.route}"


def test_router_simple_ack():
    d = classify_utterance("okay")
    assert d.route == ROUTE_BACKCHANNEL


def test_router_conversational_question():
    d = classify_utterance("What does this feature do?")
    assert d.route == ROUTE_ANSWER


def test_router_complex_browser_task():
    d = classify_utterance("Open analytics and compare March with April")
    assert d.route == ROUTE_TASK_HANDOFF
    assert d.reason == "browser_task"


def test_router_browser_verb_show():
    d = classify_utterance("Show me the pricing page")
    assert d.route == ROUTE_TASK_HANDOFF


def test_router_complex_instruction_no_verb():
    # >= 10 words, no browser verb → task handoff (complex_instruction)
    d = classify_utterance(
        "I am curious about why the integration keeps failing every single day lately"
    )
    assert d.route == ROUTE_TASK_HANDOFF
    assert d.reason == "complex_instruction"


def test_router_agent_busy_short_becomes_backchannel():
    # Short utterance while agent is working should not create a new task
    d = classify_utterance("okay cool", agent_working=True)
    assert d.route == ROUTE_BACKCHANNEL


def test_router_task_handoff_has_ack_hint():
    d = classify_utterance("Navigate to the dashboard settings")
    assert d.route == ROUTE_TASK_HANDOFF
    assert d.ack_hint  # non-empty for task_handoff


def test_router_answer_no_ack_hint():
    d = classify_utterance("What is this platform for?")
    assert d.route == ROUTE_ANSWER
    assert d.ack_hint == ""


# ── Orchestrator ───────────────────────────────────────────────────────────


def _make_orch():
    """Build an AgentOrchestrator with all external I/O mocked out."""
    from navigator.agent_runtime.orchestrator import AgentOrchestrator

    session = AgentSession(session_id=uuid4(), product_id="test-product")
    graph = MagicMock()
    graph.pages = {"home": MagicMock()}

    with (
        patch("navigator.agent_runtime.orchestrator.FlashPlanner"),
        patch("navigator.agent_runtime.orchestrator.GroqEventWorker"),
        patch("navigator.agent_runtime.orchestrator.build_dom_state", return_value={}),
    ):
        orch = AgentOrchestrator(
            session=session,
            graph=graph,
            page=MagicMock(),
            log=MagicMock(),
            page_id="home",
        )
    return orch


def test_orchestrator_backchannel_does_not_start_task():
    orch = _make_orch()
    decision = orch.handle_utterance("okay")
    assert decision.route == ROUTE_BACKCHANNEL
    # Worker thread should NOT have been started
    assert not orch.is_working


def test_orchestrator_answer_does_not_start_task():
    orch = _make_orch()
    decision = orch.handle_utterance("What does this feature do?")
    assert decision.route == ROUTE_ANSWER
    assert not orch.is_working


def test_orchestrator_task_handoff_sets_pending_goal():
    orch = _make_orch()
    # Planner returns None so _execute_task exits quickly
    orch.planner.plan.return_value = None  # type: ignore[attr-defined]
    orch.handle_utterance("Open the analytics dashboard and show me the March data")
    # Give the daemon thread a moment to start and take the lock
    deadline = time.time() + 1.0
    while not orch.is_working and time.time() < deadline:
        time.sleep(0.01)
    # Route was task_handoff
    assert orch.store.state.conversation.last_user_message == \
        "Open the analytics dashboard and show me the March data"


def test_orchestrator_no_recursive_task_on_interrupt():
    """When a task is running, a second utterance must NOT spawn another thread."""
    orch = _make_orch()

    gate = threading.Event()  # keeps the first "task" alive

    def slow_plan(*args, **kwargs):
        gate.wait(timeout=2.0)
        return None

    orch.planner.plan.side_effect = slow_plan  # type: ignore[attr-defined]

    # Start a task
    orch.handle_utterance("Navigate to the dashboard settings")
    # Wait for worker to grab the lock
    deadline = time.time() + 1.0
    while not orch.is_working and time.time() < deadline:
        time.sleep(0.01)

    assert orch.is_working, "Worker should be running"

    # Second utterance while worker is busy
    orch.handle_utterance("Actually show me the pricing page instead")

    # There must still be only ONE worker thread (the running one)
    rt_threads = [t for t in threading.enumerate() if t.name == "agent-runtime-worker"]
    assert len(rt_threads) <= 1, f"Expected ≤1 worker thread, got {len(rt_threads)}"

    # Pending goal should be set
    assert orch._pending_goal  # type: ignore[attr-defined]
    assert orch._interrupt_flag.is_set()  # type: ignore[attr-defined]

    # Release the gate
    gate.set()


def test_orchestrator_does_not_speak_step_result_when_verification_fails():
    """A failed action must never leak its completion narration to the transcript."""
    orch = _make_orch()
    spoken: list[str] = []
    orch.speak = spoken.append
    step = AgentAction(tool="click", spoken="The contact was created.")
    plan = AgentPlan(task_id=uuid4(), goal="Create a contact", steps=[step])

    with (
        patch("navigator.agent_runtime.orchestrator.execute_action", return_value=(None, None, "home", None)),
        patch(
            "navigator.agent_runtime.orchestrator.build_verification",
            return_value=VerificationResult(action_id=step.action_id, passed=False),
        ),
        patch.object(orch, "refresh_browser_state"),
    ):
        orch._execute_plan(plan)

    assert "The contact was created." not in spoken
    assert spoken == ["That step didn't verify — I'll try another approach."]


def test_gated_playlist_reuses_asked_phone_across_flows():
    """Flow B resolves Flow A's visitor value without prompting a second time."""
    from navigator.agent.recorded_playback import run_demo_playlist_gated
    from navigator.agent.state import CallDeps

    phone_step = DemoStep(
        id="ask-phone",
        action=DemoStepAction(
            tool="type", target=SemanticTarget(semantic_id="phone_field"), value="{{phone}}"
        ),
        verification=DemoStepVerification(),
        interaction=DemoStepInteraction(
            mode=InteractionMode.ask,
            input_name="phone",
            input_type="phone",
            prompt="What phone number should I use?",
        ),
        recovery=DemoStepRecovery(on_failure=RecoveryPolicy.fail),
    )
    reuse_step = phone_step.model_copy(
        update={
            "id": "reuse-phone",
            "action": DemoStepAction(
                tool="type", target=SemanticTarget(semantic_id="confirm_phone"), value="{{phone}}"
            ),
            "interaction": DemoStepInteraction(
                mode=InteractionMode.ask,
                input_name="phone",
                input_type="phone",
                prompt="What phone number should I use?",
            ),
        }
    )
    demo_graph = DemoGraph(
        flows={"collect": DemoFlow(flow_id="collect", steps=[phone_step]), "reuse": DemoFlow(flow_id="reuse", steps=[reuse_step])},
        playlist=DemoPlaylist(flows=["collect", "reuse"]),
    )
    page = MagicMock()
    page.url = "https://acme.test/"
    action_values: list[str] = []
    prompts: list[str] = []
    events: list[tuple[object, dict]] = []

    def _execute(*, action, **_kwargs):
        action_values.append(action.value)
        return None, ToolResult(ok=True, tool="fill_field", detail="ok", duration_ms=1), "home", None

    deps = CallDeps(
        graph=MagicMock(), page=page, log=MagicMock(), speaker=MagicMock(), product_id="acme",
        listen_once=lambda _prompt: "+1 (555) 010-2000",
    )
    with (
        patch("navigator.agent_runtime.execution.demo_step_executor.execute_action", side_effect=_execute),
        patch("navigator.agent_runtime.execution.demo_step_executor.wait_settled", return_value=True),
        patch("navigator.agent_runtime.execution.demo_step_executor.verify_step", return_value=(True, "ok")),
        patch("navigator.agent_runtime.execution.demo_step_executor._dom_fingerprint_before", return_value="before"),
    ):
        result = run_demo_playlist_gated(
            deps,
            session_id=uuid4(),
            demo_graph=demo_graph,
            speak=prompts.append,
            emit=lambda kind, **kwargs: events.append((kind, kwargs)),
        )

    assert result["failed"] == 0, [e.developer_message for e in result["errors"]]
    assert result["completed"] == 2
    assert action_values == ["+1 (555) 010-2000", "+1 (555) 010-2000"]
    assert prompts == ["What phone number should I use?"]


# ── Live bridge ────────────────────────────────────────────────────────────


def test_bridge_returns_none_when_runtime_disabled():
    from navigator.agent_runtime.bridge import on_live_heard

    deps = MagicMock()
    deps.orchestrator = None
    result = on_live_heard(deps, "hello")
    assert result is None


def test_bridge_backchannel_route():
    from navigator.agent_runtime.bridge import on_live_heard

    orch = _make_orch()
    deps = MagicMock()
    deps.orchestrator = orch
    result = on_live_heard(deps, "okay")
    assert result is not None
    assert result.route == ROUTE_BACKCHANNEL


def test_bridge_task_handoff_route():
    from navigator.agent_runtime.bridge import on_live_heard

    orch = _make_orch()
    orch.planner.plan.return_value = None  # type: ignore[attr-defined]
    deps = MagicMock()
    deps.orchestrator = orch
    result = on_live_heard(deps, "Navigate to the pricing page and click upgrade")
    assert result is not None
    assert result.route == ROUTE_TASK_HANDOFF


# ── LiveAdapter speech semantics ────────────────────────────────────────────


def test_live_adapter_acknowledge_uses_acknowledge_mode():
    from navigator.agent_runtime.adapters.live_adapter import LiveAdapter

    live = MagicMock()
    adapter = LiveAdapter(live)
    adapter.acknowledge("Let me check that")
    live.say.assert_called_once_with("Let me check that", mode="acknowledge")


def test_live_adapter_speak_result_uses_result_mode():
    from navigator.agent_runtime.adapters.live_adapter import LiveAdapter

    live = MagicMock()
    adapter = LiveAdapter(live)
    adapter.speak_result("Here are the results.")
    live.say.assert_called_once_with("Here are the results.", mode="result")


def test_live_adapter_speak_error_uses_error_mode():
    from navigator.agent_runtime.adapters.live_adapter import LiveAdapter

    live = MagicMock()
    adapter = LiveAdapter(live)
    adapter.speak_error("I ran into an issue.")
    live.say.assert_called_once_with("I ran into an issue.", mode="error")


def test_live_adapter_acknowledge_and_result_are_separate():
    from navigator.agent_runtime.adapters.live_adapter import LiveAdapter

    live = MagicMock()
    adapter = LiveAdapter(live)
    adapter.acknowledge("Got it")
    adapter.speak_result("Done — take a look.")
    assert live.say.call_count == 2
    modes = [call.kwargs["mode"] for call in live.say.call_args_list]
    assert modes == ["acknowledge", "result"]


# ── World state ─────────────────────────────────────────────────────────────


def test_world_state_version_increments():
    from navigator.agent_runtime.world_state.store import WorldStateStore

    session = AgentSession(session_id=uuid4(), product_id="demo")
    store = WorldStateStore(AgentWorldState(session=session))
    assert store.version() == 0
    store.update(
        lambda s: s.model_copy(
            update={"conversation": s.conversation.model_copy(update={"last_user_message": "hi"})}
        )
    )
    assert store.version() == 1
    assert store.state.conversation.last_user_message == "hi"


def test_semantic_id_slug():
    el = {"tag": "button", "text": "Analytics"}
    assert semantic_id_for(el).startswith("button")
