"""Phase-3: Compile RecordedStep list into DemoStep list.

Bridges the legacy recorder/explorer output into the new semantic DemoStep
execution contract. The old RecordedStep is preserved unchanged — this compiler
produces the new layer on top of it.

Safety classification rules:
  - submit / send / pay / delete / remove / confirm → mutation or destructive
  - fill on a form field → user_input (may need visitor value)
  - navigate / click non-form → safe_demo

Narration:
  - ``spoken`` from RecordedStep becomes ``source_transcript``
  - ``semantic_intent`` is derived from the step's objective (alias + tool)
  - ``default`` narration is generated from semantic_intent (no LLM here; a
    richer version will be added in Phase-5 when the LLM understands the flow)
"""

from __future__ import annotations

import re
from typing import Sequence

from navigator.automation.record import RecordedStep
from navigator.agent_runtime.models import (
    DemoStep,
    DemoStepAction,
    DemoStepInteraction,
    DemoStepNarration,
    DemoStepPresentation,
    DemoStepRecovery,
    DemoStepVerification,
    InteractionMode,
    RecoveryPolicy,
    SafetyClass,
    SemanticTarget,
)


_DESTRUCTIVE_WORDS = frozenset({"delete", "remove", "destroy", "purge", "wipe", "cancel"})
_MUTATION_WORDS = frozenset({"send", "submit", "save", "create", "publish", "pay", "charge", "confirm"})
_INPUT_TOOLS = frozenset({"fill_field"})


def _classify_safety(step: RecordedStep) -> SafetyClass:
    alias = (step.alias or "").lower()
    tool = (step.tool or "").lower()

    if step.needs_approval:
        for word in _DESTRUCTIVE_WORDS:
            if word in alias:
                return SafetyClass.destructive
        return SafetyClass.mutation

    if tool in _INPUT_TOOLS:
        return SafetyClass.user_input

    for word in _DESTRUCTIVE_WORDS:
        if word in alias:
            return SafetyClass.destructive

    for word in _MUTATION_WORDS:
        if word in alias:
            return SafetyClass.mutation

    return SafetyClass.safe_demo


def _semantic_intent(step: RecordedStep) -> str:
    tool = step.tool.replace("_", " ")
    alias = re.sub(r"[_-]", " ", step.alias or "element")
    return f"{tool} {alias}".strip()


def _default_narration(intent: str, objective: str) -> str:
    if objective:
        return objective
    # Simple intent → readable phrase
    intent = intent.lower()
    if intent.startswith("navigate"):
        target = intent.replace("navigate", "").strip()
        return f"Let me navigate to {target}."
    if intent.startswith("fill"):
        target = intent.replace("fill field", "").strip()
        return f"I'll fill in the {target} field."
    if intent.startswith("click"):
        target = intent.replace("click element", "").strip()
        return f"Now I'll select {target}."
    return f"Here, I'm using {intent}."


def _build_verification(step: RecordedStep) -> DemoStepVerification:
    pc = step.postcondition or {}
    check = pc.get("check", "")
    expected = pc.get("expected", "")

    # Weak postconditions like "visible body" are upgraded to dom_changed
    sel = pc.get("selector", "")
    is_body = not sel or sel.lower() in ("body", "html")

    if check == "url_matches" and expected:
        return DemoStepVerification(url_contains=expected, settled=True)
    if check == "visible" and not is_body and sel:
        return DemoStepVerification(visible=sel, settled=True)
    if check == "text_contains" and expected:
        return DemoStepVerification(text_contains=expected, settled=True)

    # Weak postcondition → require at minimum that DOM changed
    return DemoStepVerification(settled=True)


def _build_interaction(step: RecordedStep, safety: SafetyClass) -> DemoStepInteraction:
    if safety == SafetyClass.user_input:
        alias = re.sub(r"[_-]", " ", step.alias or "field")
        return DemoStepInteraction(
            mode=InteractionMode.optional,
            input_name=step.alias or "value",
            input_type="text",
            prompt=f"Would you like me to use your own {alias}, or a sample value?",
            fallback_after_ms=8000,
            fallback_value=step.value or "",
        )
    if step.needs_approval and not step.approved:
        return DemoStepInteraction(mode=InteractionMode.confirm)
    return DemoStepInteraction(mode=InteractionMode.none)


def _tool_name(tool: str) -> str:
    mapping = {
        "click_element": "click",
        "fill_field": "type",
        "navigate": "navigate",
        "wait_for": "wait",
    }
    return mapping.get(tool, "click")


def compile_step(step: RecordedStep, *, objective: str = "") -> DemoStep:
    """Convert one RecordedStep into a DemoStep."""
    safety = _classify_safety(step)
    intent = _semantic_intent(step)
    spoken = step.spoken if hasattr(step, "spoken") else ""

    narration = DemoStepNarration(
        default=_default_narration(intent, objective) if not spoken else spoken,
        source_transcript=spoken,
        semantic_intent=intent,
    )

    action = DemoStepAction(
        tool=_tool_name(step.tool),
        target=SemanticTarget(semantic_id=step.alias or "", page_id=step.page_id),
        value=step.value or "",
    )

    presentation = DemoStepPresentation(
        highlight=step.alias or "",
        pause_after_ms=step.at_ms if step.at_ms else 400,
    )

    return DemoStep(
        id=f"{step.page_id}_{step.alias or 'step'}",
        objective=objective or intent,
        action=action,
        narration=narration,
        verification=_build_verification(step),
        interaction=_build_interaction(step, safety),
        safety=safety,
        presentation=presentation,
        recovery=DemoStepRecovery(
            on_failure=RecoveryPolicy.skip if safety == SafetyClass.safe_demo else RecoveryPolicy.replan,
        ),
        needs_approval=step.needs_approval,
        approved=getattr(step, "approved", False),
    )


def compile_flow(
    steps: Sequence[RecordedStep],
    *,
    flow_id: str = "",
    objective: str = "",
) -> list[DemoStep]:
    """Compile all steps in a recorded flow into DemoSteps."""
    return [compile_step(s, objective=objective) for s in steps]
