"""Tests for the 9-phase Navigator architecture migration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from navigator.agent_runtime.models import (
    DemoGraph,
    DemoMode,
    DemoSessionContext,
    DemoStep,
    DemoStepAction,
    DemoStepNarration,
    DemoStepStatus,
    DemoStepVerification,
    ExplorationWorldModel,
    InteractionMode,
    RecoveryPolicy,
    SafetyClass,
    SemanticTarget,
    SessionOutcome,
    StructuredError,
    WatchdogSlice,
)
from navigator.agent_runtime import watchdog as wd
from navigator.agent_runtime.demo_compiler import compile_step, _classify_safety
from navigator.agent_runtime.failure import determine_outcome, visitor_script
from navigator.automation.explore.discovery import (
    score_visit,
    should_abandon_branch,
    update_world_model,
)
from navigator.automation.record import RecordedStep


# ---------------------------------------------------------------------------
# Phase-1: Watchdog
# ---------------------------------------------------------------------------

class TestWatchdog:
    def test_tick_sets_timestamp(self):
        w = WatchdogSlice()
        result = wd.tick(w)
        assert result.last_action_started_at is not None

    def test_record_failure_increments(self):
        w = WatchdogSlice()
        w2 = wd.record_failure(w)
        assert w2.consecutive_failures == 1

    def test_clear_failure_resets(self):
        w = WatchdogSlice(consecutive_failures=3, loop_detected=True)
        w2 = wd.clear_failure(w)
        assert w2.consecutive_failures == 0
        assert not w2.loop_detected

    def test_loop_detected_after_repeated_state(self):
        w = WatchdogSlice()
        fp = "abc123"
        for _ in range(3):  # _MAX_LOOP_REENTRY = 2, need count >= 2
            w = wd.record_state(w, fp)
        assert w.loop_detected

    def test_is_stuck_on_failures(self):
        w = WatchdogSlice(consecutive_failures=3)
        assert wd.is_stuck(w)

    def test_is_stuck_on_loop(self):
        w = WatchdogSlice(loop_detected=True)
        assert wd.is_stuck(w)

    def test_not_stuck_clean(self):
        w = WatchdogSlice()
        assert not wd.is_stuck(w)


# ---------------------------------------------------------------------------
# Phase-3: DemoStep compiler
# ---------------------------------------------------------------------------

def _recorded(tool="click_element", alias="send_campaign", needs_approval=False, value=""):
    return RecordedStep(
        tool=tool,
        alias=alias,
        selector=f"[data-testid='{alias}']",
        value=value,
        page_id="dashboard",
        postcondition={"check": "url_matches", "expected": "/campaign"},
        needs_approval=needs_approval,
    )


class TestDemoCompiler:
    def test_safe_click_classifies_as_safe_demo(self):
        step = _recorded("click_element", "analytics_tab")
        assert _classify_safety(step) == SafetyClass.safe_demo

    def test_send_classifies_as_mutation(self):
        step = _recorded("click_element", "send_campaign", needs_approval=True)
        assert _classify_safety(step) == SafetyClass.mutation

    def test_delete_classifies_as_destructive(self):
        step = _recorded("click_element", "delete_contact", needs_approval=True)
        assert _classify_safety(step) == SafetyClass.destructive

    def test_fill_classifies_as_user_input(self):
        step = _recorded("fill_field", "phone_number", value="+91...")
        assert _classify_safety(step) == SafetyClass.user_input

    def test_compile_produces_demo_step(self):
        step = _recorded("click_element", "analytics_tab")
        ds = compile_step(step, objective="Show analytics")
        assert isinstance(ds, DemoStep)
        assert ds.objective == "Show analytics"
        assert ds.action.tool == "click"
        assert ds.safety == SafetyClass.safe_demo

    def test_weak_postcondition_upgraded(self):
        step = RecordedStep(
            tool="click_element",
            alias="some_button",
            selector="button",
            page_id="main",
            postcondition={"check": "visible", "selector": "body"},
        )
        ds = compile_step(step)
        # body-visible is weak → spec should be empty (dom_changed will verify)
        assert not ds.verification.visible

    def test_url_postcondition_preserved(self):
        step = _recorded()
        ds = compile_step(step)
        assert ds.verification.url_contains == "/campaign"


# ---------------------------------------------------------------------------
# Phase-7: DemoSessionContext
# ---------------------------------------------------------------------------

class TestDemoSessionContext:
    def test_set_and_get_known_field(self):
        ctx = DemoSessionContext()
        ctx.set("visitor_name", "Riya")
        assert ctx.get("visitor_name") == "Riya"

    def test_set_and_get_extra_field(self):
        ctx = DemoSessionContext()
        ctx.set("campaign_type", "WhatsApp")
        assert ctx.get("campaign_type") == "WhatsApp"

    def test_default_empty(self):
        ctx = DemoSessionContext()
        assert ctx.get("phone") == ""
        assert ctx.get("nonexistent") == ""


# ---------------------------------------------------------------------------
# Phase-8: Failure lifecycle
# ---------------------------------------------------------------------------

class TestFailureLifecycle:
    def test_all_completed(self):
        outcome = determine_outcome(
            completed_flows=["f1", "f2"],
            failed_flows=[],
            demo_graph=None,
            handoff_requested=False,
        )
        assert outcome == SessionOutcome.success

    def test_partial(self):
        outcome = determine_outcome(
            completed_flows=["f1"],
            failed_flows=["f2"],
            demo_graph=None,
            handoff_requested=False,
        )
        assert outcome == SessionOutcome.partial_success

    def test_all_failed(self):
        outcome = determine_outcome(
            completed_flows=[],
            failed_flows=["f1", "f2"],
            demo_graph=None,
            handoff_requested=False,
        )
        assert outcome == SessionOutcome.failed

    def test_handoff_beats_partial(self):
        outcome = determine_outcome(
            completed_flows=["f1"],
            failed_flows=[],
            demo_graph=None,
            handoff_requested=True,
        )
        assert outcome == SessionOutcome.handoff_required

    def test_visitor_script_has_name(self):
        script = visitor_script(SessionOutcome.success, visitor_name="Riya")
        assert "Riya" in script

    def test_visitor_script_failure_no_name(self):
        script = visitor_script(SessionOutcome.failed)
        assert "team" in script.lower()


# ---------------------------------------------------------------------------
# Phase-5: Discovery world model
# ---------------------------------------------------------------------------

class TestDiscovery:
    def test_score_new_page_high(self):
        world = ExplorationWorldModel()
        score = score_visit(world, url="https://x.com/new", elements=[], new_capability=True, new_page=True)
        assert score > 0.5

    def test_score_repeated_page_low(self):
        from navigator.agent_runtime import watchdog as wdg
        world = ExplorationWorldModel()
        fp = wdg.state_fingerprint("https://x.com/old", [])
        world = world.model_copy(update={"visited_states": [fp]})
        score = score_visit(world, url="https://x.com/old", elements=[], new_capability=False, new_page=False)
        assert score < 0.5

    def test_abandon_low_score_branch(self):
        world = ExplorationWorldModel(branch_scores={"main": 0.01})
        assert should_abandon_branch(world, "main")

    def test_keep_high_score_branch(self):
        world = ExplorationWorldModel(branch_scores={"main": 0.8})
        assert not should_abandon_branch(world, "main")
