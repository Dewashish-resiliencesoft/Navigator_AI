"""Groq flow picker: chooses a named flow; never invents tool calls."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError

from navigator.memory.retrieval import Correction
from navigator.schemas import Persona

MODEL = "llama-3.3-70b-versatile"


class FlowChoice(BaseModel):
    model_config = ConfigDict(frozen=True)

    flow_id: str
    spoken_response: str


def parse_flow_choice(raw: str, *, allowed: set[str]) -> FlowChoice:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"planner returned non-JSON: {raw!r}") from e
    try:
        choice = FlowChoice.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"planner returned invalid FlowChoice: {raw!r}") from e
    if choice.flow_id not in allowed:
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
) -> str:
    corr_lines = [c.rule for c in corrections] or ["(none)"]
    know_lines = list(knowledge) or ["(none)"]
    lines = [
        f"You are {persona.agent_name}, demoing {persona.product_name}.",
        f"Tone: {persona.tone}",
        f"Current page_id: {page_id}",
        f"Allowed flow_ids (pick exactly one): {', '.join(flow_ids)}",
        'Return ONLY JSON: {"flow_id": "...", "spoken_response": "..."}',
        "Do not invent steps, selectors, or flows outside the allowed list.",
        "Transcript:",
        *transcript,
        "Corrections:",
        *corr_lines,
        "Product knowledge:",
        *know_lines,
    ]
    if retry_hint:
        lines.append(retry_hint)
    return "\n".join(lines)


def _groq_complete(api_key: str, prompt: str) -> str:
    from groq import Groq

    client = Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model=MODEL,
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
            retry_hint=(
                f"Previous answer was invalid. flow_id MUST be one of: "
                f"{', '.join(sorted(allowed))}"
            ),
        )
        raw2 = completer(retry)
        return parse_flow_choice(raw2, allowed=allowed)
