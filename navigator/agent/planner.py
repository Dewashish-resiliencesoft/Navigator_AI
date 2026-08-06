"""Groq flow picker: chooses a named flow or handoff; never invents tool calls."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from navigator.meeting.intake import ProspectIntake
from navigator.knowledge.memory.retrieval import Correction
from navigator.core.schemas import Persona

def _planning_model() -> str:
    from navigator.core.settings import settings

    return settings.brain_planning_model

#: Spoken when prospect asks for something outside the site-graph allow-list.
HANDOFF_SPOKEN = (
    "In this demo we can't show you that — it's confidential / outside what I'm "
    "allowed to demo. If you want that configured or more info, we can arrange it. "
    "I'll bring in a human agent who can handle things outside my scope."
)

HANDOFF_TOKENS = frozenset({"", "__handoff__", "null", "none"})


class FlowChoice(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: Named flow on the current page, or None → out-of-scope human handoff.
    flow_id: str | None
    spoken_response: str

    @field_validator("flow_id", mode="before")
    @classmethod
    def _normalize_handoff(cls, v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, str) and v.strip().lower() in HANDOFF_TOKENS:
            return None
        return str(v) if v is not None else None


def parse_flow_choice(raw: str, *, allowed: set[str]) -> FlowChoice:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"planner returned non-JSON: {raw!r}") from e
    try:
        choice = FlowChoice.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"planner returned invalid FlowChoice: {raw!r}") from e
    if choice.flow_id is not None and choice.flow_id not in allowed:
        raise ValueError(
            f"flow_id {choice.flow_id!r} not in allowed {sorted(allowed)}"
        )
    return choice


def build_prompt(
    *,
    page_id: str,
    flow_ids: Sequence[str],
    transcript: Sequence[str],
    corrections: Sequence[Correction],
    knowledge: Sequence[str],
    persona: Persona,
    retry_hint: str | None = None,
    intake: ProspectIntake | None = None,
    product_brief: str = "",
    screen_context: str = "",
) -> str:
    corr_lines = [c.rule for c in corrections] or ["(none)"]
    know_lines = list(knowledge) or ["(none)"]
    lines = [
        f"You are {persona.agent_name}, a live product specialist demoing "
        f"{persona.product_name}.",
        f"Tone: {persona.tone}",
        "Speak as that product's expert: acknowledge the prospect, use what is "
        "on screen, and tie answers to their stated need.",
        f"Current page_id: {page_id}",
        f"Allowed flow_ids (pick exactly one): {', '.join(flow_ids)}",
        "If the user asks for something NOT in the allowed list, set flow_id to null "
        "(out-of-scope handoff). Never invent flows or selectors.",
        'Return ONLY JSON: {"flow_id": "<id>"|null, "spoken_response": "..."}',
        "spoken_response: 1–2 short spoken sentences, natural contractions.",
    ]
    if product_brief.strip():
        lines.extend(["Product brief:", product_brief.strip()[:4500]])
    if screen_context.strip():
        lines.extend(
            ["What is on screen right now:", screen_context.strip()[:1200]]
        )
    lines.extend(
        [
            "Transcript:",
            *transcript,
            "Corrections:",
            *corr_lines,
            "Product knowledge (retrieved):",
            *know_lines,
        ]
    )
    if intake is not None:
        lines.extend(
            [
                "Prospect intake (USE THIS — sound like you remember them):",
                f"name={intake.name!r} company={intake.company!r} "
                f"business_type={intake.business_type!r} looking_for={intake.looking_for!r}",
                "spoken_response MUST address them by name when known, and tie the "
                "chosen flow to looking_for / what they just said.",
                "Prefer an allowed flow_id that best matches looking_for when several fit.",
            ]
        )
    if retry_hint:
        lines.append(retry_hint)
    return "\n".join(lines)


def _groq_complete(api_key: str, prompt: str) -> str:
    from groq import Groq

    client = Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model=_planning_model(),
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = resp.choices[0].message.content
    if not content:
        raise ValueError("Groq returned empty content")
    return content


def choose_flow(
    *,
    api_key: str,
    page_id: str,
    flow_ids: Sequence[str],
    transcript: Sequence[str],
    corrections: Sequence[Correction],
    knowledge: Sequence[str],
    persona: Persona,
    complete: Callable[[str], str] | None = None,
    intake: ProspectIntake | None = None,
    product_brief: str = "",
    screen_context: str = "",
) -> FlowChoice:
    if not flow_ids:
        raise RuntimeError(f"page {page_id!r} has no flows to choose from")
    allowed = set(flow_ids)
    completer = complete or (lambda prompt: _groq_complete(api_key, prompt))

    prompt = build_prompt(
        page_id=page_id,
        flow_ids=flow_ids,
        transcript=transcript,
        corrections=corrections,
        knowledge=knowledge,
        persona=persona,
        intake=intake,
        product_brief=product_brief,
        screen_context=screen_context,
    )
    raw = completer(prompt)
    try:
        return parse_flow_choice(raw, allowed=allowed)
    except ValueError:
        retry = build_prompt(
            page_id=page_id,
            flow_ids=flow_ids,
            transcript=transcript,
            corrections=corrections,
            knowledge=knowledge,
            persona=persona,
            intake=intake,
            product_brief=product_brief,
            screen_context=screen_context,
            retry_hint=(
                f"Previous answer was invalid. flow_id MUST be null (handoff) or "
                f"one of: {', '.join(sorted(allowed))}"
            ),
        )
        raw2 = completer(retry)
        return parse_flow_choice(raw2, allowed=allowed)
