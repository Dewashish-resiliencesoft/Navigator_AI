"""Run confirmed AgentTask steps at live-demo time (DemoContext = live_answers).

v1 verbs map onto existing ask/fill plumbing — recording never executes these.
"""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from typing import Any

from navigator.automation.prompt_command import AgentTask, AgentTaskStep
from navigator.core.schemas import FillField


def run_agent_task(
    task: AgentTask | dict[str, Any],
    *,
    live_answers: MutableMapping[str, str],
    listen_once: Callable[[str], str] | None = None,
    speak: Callable[[str], None] | None = None,
    fill_selector: Callable[[str, str], None] | None = None,
) -> list[str]:
    """Execute ask_user / save_variable / fill_field / use_variable.

    Returns a list of human-readable result notes for tracing.
    """
    if isinstance(task, dict):
        task = AgentTask.from_dict(task)
    notes: list[str] = []
    for step in task.steps:
        notes.append(_run_step(step, live_answers, listen_once, speak, fill_selector))
    return notes


def _run_step(
    step: AgentTaskStep,
    live_answers: MutableMapping[str, str],
    listen_once: Callable[[str], str] | None,
    speak: Callable[[str], None] | None,
    fill_selector: Callable[[str, str], None] | None,
) -> str:
    var = (step.variable or "").strip()
    if step.op == "ask_user":
        q = (step.question or "").strip() or f"What is your {var.replace('_', ' ')}?"
        if speak:
            speak(q)
        heard = ""
        if listen_once:
            heard = (listen_once(q) or "").strip()
        if heard and var:
            live_answers[var] = heard
            return f"ask_user {var}={heard!r}"
        return f"ask_user {var} (no answer)"
    if step.op == "save_variable":
        if var and var in live_answers:
            return f"save_variable {var} already set"
        return f"save_variable {var} (noop — use ask_user)"
    if step.op in {"fill_field", "use_variable"}:
        value = (live_answers.get(var) or "").strip() if var else ""
        if not value:
            return f"{step.op} {var} missing — pause/re-ask expected upstream"
        sel = (step.selector or "").strip()
        if fill_selector and sel:
            fill_selector(sel, value)
            return f"{step.op} {sel} ← {var}"
        # Without a selector hook, leave for resolve_demo_fill on the flow step.
        return f"{step.op} {var}={value!r} (context only)"
    return f"skip {step.op}"


def fill_field_from_context(
    call: FillField,
    live_answers: MutableMapping[str, str],
) -> FillField | None:
    """If call has value_ref and DemoContext has it, return filled copy."""
    ref = (call.value_ref or "").strip()
    if not ref:
        return None
    cached = (live_answers.get(ref) or "").strip()
    if not cached:
        return None
    return call.model_copy(update={"value": cached, "source": "agent"})
