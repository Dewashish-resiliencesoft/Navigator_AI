"""Phase-7: Human Interaction Engine.

Handles all visitor input during a live demo:
  AUTO         — use demo fixture value silently
  ASK          — always ask visitor for value before proceeding
  OPTIONAL     — ask, but fall back to fixture after timeout
  CONFIRM      — ask visitor to confirm before executing a mutation
  MANUAL_HANDOFF — pause demo, hand control to a human

Input types: TEXT, PHONE, EMAIL, NAME, COMPANY, DATE, NUMBER, SELECTION, CONFIRMATION

DemoSessionContext stores collected values for the demo session only.
Nothing is persisted to Chroma or any permanent store.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from navigator.agent_runtime.models import (
    AgentEventKind,
    DemoSessionContext,
    DemoStep,
    DemoStepInteraction,
    InteractionMode,
    SessionOutcome,
)


class InteractionResult:
    def __init__(self, value: str, timed_out: bool = False, declined: bool = False) -> None:
        self.value = value
        self.timed_out = timed_out
        self.declined = declined


class InteractionEngine:
    """Manages visitor input collection during a live demo.

    ``speak`` delivers prompts to the visitor via TTS.
    ``wait_for_utterance`` blocks until STT returns the next visitor reply.
    Both are injected so this module stays free of audio/TTS concerns.
    """

    def __init__(
        self,
        *,
        speak: Callable[[str], None],
        wait_for_utterance: Callable[[float], str | None],
        emit: Callable,
        session_context: DemoSessionContext,
    ) -> None:
        self._speak = speak
        self._wait = wait_for_utterance
        self._emit = emit
        self._ctx = session_context

    def resolve(
        self,
        step: DemoStep,
        *,
        world_speak: Callable[[str], None] | None = None,
    ) -> InteractionResult:
        """Resolve visitor input for a step based on its interaction spec."""
        spec = step.interaction

        if spec.mode == InteractionMode.none or spec.mode == InteractionMode.auto:
            return InteractionResult(value=spec.fallback_value or "")

        # Check session context first — visitor may have already given this
        cached = self._ctx.get(spec.input_name)
        if cached and spec.mode not in (InteractionMode.ask, InteractionMode.confirm):
            return InteractionResult(value=cached)

        if spec.mode == InteractionMode.confirm:
            return self._confirm(step)

        if spec.mode == InteractionMode.manual_handoff:
            return self._handoff(step)

        # ASK or OPTIONAL
        return self._ask(step)

    def _ask(self, step: DemoStep) -> InteractionResult:
        spec = step.interaction
        prompt = spec.prompt or f"What {spec.input_name.replace('_', ' ')} would you like me to use?"
        self._speak(prompt)
        self._emit(AgentEventKind.INTERACTION_REQUESTED, payload={
            "step_id": step.id, "input_name": spec.input_name, "mode": spec.mode.value,
        })

        timeout_s = spec.fallback_after_ms / 1000.0
        reply = self._wait(timeout_s)

        if reply:
            value = _extract_value(reply, spec.input_type)
            self._ctx.set(spec.input_name, value)
            self._emit(AgentEventKind.INTERACTION_RESOLVED, payload={
                "step_id": step.id, "timed_out": False,
            })
            return InteractionResult(value=value)

        # Timed out → use fallback if OPTIONAL, else fail
        self._emit(AgentEventKind.INTERACTION_TIMED_OUT, payload={"step_id": step.id})
        if spec.mode == InteractionMode.optional and spec.fallback_value:
            fallback_msg = f"I'll use a sample {spec.input_name.replace('_', ' ')} instead."
            self._speak(fallback_msg)
            return InteractionResult(value=spec.fallback_value, timed_out=True)

        return InteractionResult(value="", timed_out=True)

    def _confirm(self, step: DemoStep) -> InteractionResult:
        spec = step.interaction
        prompt = spec.prompt or f"I'm about to {step.objective}. Should I proceed?"
        self._speak(prompt)
        self._emit(AgentEventKind.INTERACTION_REQUESTED, payload={
            "step_id": step.id, "mode": "confirm",
        })

        timeout_s = spec.fallback_after_ms / 1000.0
        reply = (self._wait(timeout_s) or "").lower().strip()

        confirmed = any(w in reply for w in ("yes", "yeah", "sure", "ok", "proceed", "go"))
        declined = any(w in reply for w in ("no", "nope", "skip", "stop", "cancel"))

        self._emit(AgentEventKind.INTERACTION_RESOLVED, payload={
            "step_id": step.id, "confirmed": confirmed,
        })
        return InteractionResult(value="confirmed" if confirmed else "", declined=declined)

    def _handoff(self, step: DemoStep) -> InteractionResult:
        msg = "Let me pause here and hand over to our team who can help you directly."
        self._speak(msg)
        self._emit(AgentEventKind.SESSION_HANDOFF, payload={"step_id": step.id})
        return InteractionResult(value="", declined=True)


def _extract_value(utterance: str, input_type: str) -> str:
    """Basic extraction — takes the whole utterance for most types."""
    utterance = utterance.strip()
    if input_type == "phone":
        import re
        digits = re.sub(r"[^\d+\-\(\) ]", "", utterance)
        return digits.strip() or utterance
    if input_type == "email":
        import re
        match = re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", utterance, re.I)
        return match.group(0) if match else utterance
    if input_type == "number":
        import re
        match = re.search(r"\d[\d,]*", utterance)
        return match.group(0) if match else utterance
    return utterance
