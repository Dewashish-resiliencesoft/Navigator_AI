"""Resolve FillField values that need the End User's live answer.

`source="user"` is the requires_live_input marker (see FillField). EXECUTING
calls `resolve_live_fill` before Playwright so a business-specific field never
auto-fills the Client's setup example unless the prospect's answer is unclear.

`value_ref` reuses an answer collected earlier in the same call (alias → heard).
"""

from __future__ import annotations

import re
from collections.abc import Callable, MutableMapping

from navigator.core.schemas import FillField, Postcondition

_UNCLEAR = re.compile(
    r"^\s*(um+|uh+|erm+|hmm+|huh\??|what\??|sorry|idk|i don'?t know|"
    r"not sure|skip|pass)?\s*$",
    re.I,
)


_AFFIRM = re.compile(
    r"\b(yes|yeah|yep|yup|sure|correct|confirm(ed)?|go ahead|proceed|submit|"
    r"send|ok|okay|looks good|perfect|all good|that'?s right|do it)\b",
    re.I,
)


def is_affirmative(text: str) -> bool:
    """True when the End User approved a confirm-before step."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_AFFIRM.search(t))


def needs_live_input(call: FillField) -> bool:
    if (call.value_ref or "").strip():
        return False
    return call.source == "user"


def live_prompt(call: FillField) -> str:
    q = (call.live_question or "").strip()
    if q:
        return q
    alias = call.selector.replace("_", " ").replace("-", " ")
    return f"What should I put in {alias}?"


def is_unclear(text: str) -> bool:
    return not text.strip() or bool(_UNCLEAR.match(text.strip()))


def resolve_demo_fill(
    call: FillField,
    *,
    live_answers: MutableMapping[str, str] | None,
    listen_once: Callable[[str], str] | None,
    extract_entity: Callable[..., str] | None,
    speak: Callable[[str], None] | None = None,
) -> tuple[FillField, str]:
    """Resolve value_ref reuse, then source=user ask, else leave agent fill."""
    answers: MutableMapping[str, str] = live_answers if live_answers is not None else {}
    ref = (call.value_ref or "").strip()
    if ref:
        cached = (answers.get(ref) or "").strip()
        if cached:
            return _with_value(call, cached), f"value_ref {ref}={cached!r}"
        # Variable not collected yet (or empty). Re-ask, but keep the recorded
        # sample as the fallback so a missing/unclear answer never wedges the
        # flow. The ask copy must carry a real fallback, not the {{ref}} token.
        sample = call.fallback_value if call.fallback_value is not None else ""
        if not str(sample).strip():
            v = str(call.value or "").strip()
            sample = "" if v.startswith("{{") and v.endswith("}}") else v
        ask = call.model_copy(
            update={
                "source": "user",
                "live_question": call.live_question
                or f"What is your {ref.replace('_', ' ')}?",
                "fallback_value": sample,
            }
        )
        updated, detail = resolve_live_fill(
            ask,
            listen_once=listen_once,
            extract_entity=extract_entity,
            speak=speak,
        )
        if updated.value:
            answers[ref] = updated.value
        return updated, detail

    if needs_live_input(call):
        updated, detail = resolve_live_fill(
            call,
            listen_once=listen_once,
            extract_entity=extract_entity,
            speak=speak,
        )
        alias = (call.selector or "").strip()
        if alias and updated.value:
            answers[alias] = updated.value
        return updated, detail
    return call, "agent fill"


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
    example = call.fallback_value if call.fallback_value is not None else call.value
    heard = _ask(listen_once, speak, prompt)
    cleaned = _extract(extract_entity, prompt, heard)

    if is_unclear(cleaned):
        reask = f"Sorry, I didn't catch that. {prompt}"
        heard2 = _ask(listen_once, speak, reask)
        cleaned = _extract(extract_entity, prompt, heard2)
        if is_unclear(cleaned):
            # Tell the End User why their data isn't used, then use the sample.
            if speak is not None and str(example or "").strip():
                try:
                    speak(
                        "No problem — I couldn't quite catch that, so I'll use a "
                        "sample value here and we can keep going."
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[live_input] speak fallback notice failed: {exc}", flush=True)
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
