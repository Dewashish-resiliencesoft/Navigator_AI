"""Resolve FillField values that need the End User's live answer.

`source="user"` is the requires_live_input marker (see FillField). EXECUTING
calls `resolve_live_fill` before Playwright so a business-specific field never
auto-fills the Client's setup example unless the prospect's answer is unclear.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from navigator.core.schemas import FillField, Postcondition

_UNCLEAR = re.compile(
    r"^\s*(um+|uh+|erm+|hmm+|huh\??|what\??|sorry|idk|i don'?t know|"
    r"not sure|skip|pass)?\s*$",
    re.I,
)


def needs_live_input(call: FillField) -> bool:
    return call.source == "user"


def live_prompt(call: FillField) -> str:
    q = (call.live_question or "").strip()
    if q:
        return q
    alias = call.selector.replace("_", " ").replace("-", " ")
    return f"What should I put in {alias}?"


def is_unclear(text: str) -> bool:
    return not text.strip() or bool(_UNCLEAR.match(text.strip()))


def resolve_live_fill(
    call: FillField,
    *,
    listen_once: Callable[[str], str] | None,
    extract_entity: Callable[..., str] | None,
    speak: Callable[[str], None] | None = None,
) -> tuple[FillField, str]:
    """Ask once, re-ask once on unclear, then fall back to the example `value`.

    Returns (updated FillField, detail for DecisionTrace).
    """
    prompt = live_prompt(call)
    example = call.value
    heard = _ask(listen_once, speak, prompt)
    cleaned = _extract(extract_entity, prompt, heard)

    if is_unclear(cleaned):
        reask = f"Sorry, I didn't catch that. {prompt}"
        heard2 = _ask(listen_once, speak, reask)
        cleaned = _extract(extract_entity, prompt, heard2)
        if is_unclear(cleaned):
            updated = _with_value(call, example)
            return updated, f"live_input unclear after re-ask; used example {example!r}"

    updated = _with_value(call, cleaned)
    return updated, f"live_input filled {call.selector}={cleaned!r}"


def _ask(
    listen_once: Callable[[str], str] | None,
    speak: Callable[[str], None] | None,
    prompt: str,
) -> str:
    if speak is not None:
        try:
            speak(prompt)
        except Exception as exc:  # noqa: BLE001
            print(f"[live_input] speak failed: {exc}", flush=True)
    if listen_once is None:
        return ""
    try:
        return (listen_once(prompt) or "").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[live_input] listen failed: {exc}", flush=True)
        return ""


def _extract(
    extract_entity: Callable[..., str] | None, question: str, heard: str
) -> str:
    if not heard.strip():
        return ""
    if extract_entity is None:
        try:
            from navigator.meeting.intake import extract_intake_entity

            return (extract_intake_entity("looking_for", question, heard) or "").strip()
        except Exception as exc:  # noqa: BLE001
            print(f"[live_input] extract failed: {exc}", flush=True)
            return heard.strip()
    try:
        return (extract_entity("live_field", question, heard) or "").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[live_input] extract failed: {exc}", flush=True)
        return heard.strip()


def _with_value(call: FillField, value: str) -> FillField:
    expects = call.expects
    if expects.check == "value_equals":
        expects = Postcondition(
            check=expects.check,
            selector=expects.selector,
            expected=value,
            timeout_ms=expects.timeout_ms,
        )
    return call.model_copy(update={"value": value, "expects": expects})
