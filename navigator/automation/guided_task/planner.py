"""Parse a natural-language Agent Task into a multi-flow guided plan."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from navigator.automation.guided_task.models import GuidedFlow, GuidedPlan, GuidedStep
from navigator.automation.record import _slug

_PLAN_PROMPT = """You are helping a SaaS company plan a product demo recording.

The client describes what the demo should cover. Split it into 2–6 logical demo
sections (flows). Each section has ordered steps.

Step kinds:
- USER_INPUT — pause during live demo to ask the visitor (phone, email, name…).
  Never type real visitor data while recording.
- ACTION — click, open, navigate, create, save (agent performs during guided record).

Reply with JSON only:
{{
  "flows": [
    {{
      "name": "Short section title",
      "steps": [
        {{
          "kind": "USER_INPUT"|"ACTION",
          "label": "human-readable step",
          "alias": "snake_case_element_name",
          "live_question": "spoken question for USER_INPUT only",
          "spoken": "one line host says while doing this",
          "action_hint": "what to click/do for ACTION only"
        }}
      ]
    }}
  ]
}}

Client task:
{task}
"""

_USER_INPUT_RE = re.compile(
    r"\b(ask|collect|get|request|enter|phone|email|name|number|otp|verify)\b",
    re.I,
)


def _slug_alias(text: str, fallback: str) -> str:
    return _slug(text, fallback)[:40]


def _heuristic_plan(prompt: str) -> GuidedPlan:
    """No LLM — split on sentences and group every ~3 steps into a flow."""
    task_id = GuidedPlan.new_id()
    chunks = [
        c.strip()
        for c in re.split(r"[.\n;]+", prompt)
        if c.strip() and len(c.strip()) > 3
    ]
    if not chunks:
        chunks = [prompt.strip() or "demo walkthrough"]

    steps: list[GuidedStep] = []
    for i, chunk in enumerate(chunks):
        alias = _slug_alias(chunk, f"step_{i + 1}")
        if _USER_INPUT_RE.search(chunk):
            steps.append(
                GuidedStep(
                    kind="USER_INPUT",
                    label=chunk[:120],
                    alias=alias,
                    live_question=f"Could you share your {alias.replace('_', ' ')}?",
                    spoken=chunk[:200],
                )
            )
        else:
            steps.append(
                GuidedStep(
                    kind="ACTION",
                    label=chunk[:120],
                    alias=alias,
                    spoken=chunk[:200],
                    action_hint=chunk[:200],
                )
            )

    flows: list[GuidedFlow] = []
    per_flow = max(2, min(5, len(steps)))
    for fi in range(0, len(steps), per_flow):
        batch = steps[fi : fi + per_flow]
        fname = batch[0].label[:48] or f"Section {len(flows) + 1}"
        fid = _slug_alias(fname, f"guided_flow_{len(flows) + 1}")
        flows.append(
            GuidedFlow(
                name=fname,
                flow_id=fid,
                page_id="dashboard",
                steps=tuple(batch),
            )
        )

    return GuidedPlan(task_id=task_id, prompt=prompt.strip(), flows=tuple(flows))


def _parse_llm_plan(raw: str, prompt: str) -> GuidedPlan | None:
    match = re.search(r"\{.*\}", (raw or "").strip(), re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    flows: list[GuidedFlow] = []
    for fi, f in enumerate(data.get("flows") or []):
        if not isinstance(f, dict):
            continue
        fname = str(f.get("name") or f"Section {fi + 1}").strip()
        fid = _slug_alias(fname, f"guided_flow_{fi + 1}")
        steps: list[GuidedStep] = []
        for si, s in enumerate(f.get("steps") or []):
            if not isinstance(s, dict):
                continue
            kind = str(s.get("kind") or "ACTION").strip().upper()
            if kind not in {"USER_INPUT", "ACTION"}:
                kind = "ACTION"
            label = str(s.get("label") or "").strip() or f"Step {si + 1}"
            alias = _slug_alias(str(s.get("alias") or label), f"step_{si + 1}")
            steps.append(
                GuidedStep(
                    kind=kind,  # type: ignore[arg-type]
                    label=label,
                    alias=alias,
                    live_question=(
                        str(s.get("live_question")).strip()
                        if s.get("live_question")
                        else None
                    ),
                    spoken=str(s.get("spoken") or label).strip(),
                    action_hint=str(s.get("action_hint") or "").strip(),
                )
            )
        if steps:
            flows.append(
                GuidedFlow(
                    name=fname,
                    flow_id=fid,
                    page_id="dashboard",
                    steps=tuple(steps),
                )
            )

    if not flows:
        return None
    return GuidedPlan(task_id=GuidedPlan.new_id(), prompt=prompt.strip(), flows=tuple(flows))


def plan_from_task(
    prompt: str,
    *,
    ask_text: Callable[[str], str] | None = None,
) -> GuidedPlan:
    """Build a guided plan from the Client's task prompt."""
    cleaned = (prompt or "").strip()
    if not cleaned:
        raise ValueError("task prompt is required")

    if ask_text is not None:
        try:
            raw = ask_text(_PLAN_PROMPT.format(task=cleaned))
            parsed = _parse_llm_plan(raw, cleaned)
            if parsed is not None and parsed.flows:
                return parsed
        except Exception as exc:  # noqa: BLE001
            print(f"[guided] LLM plan failed, using heuristic: {exc}", flush=True)

    return _heuristic_plan(cleaned)
