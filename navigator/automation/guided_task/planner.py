"""Parse a natural-language Agent Task into a single guided demo flow."""

from __future__ import annotations

import json
import re
from typing import Callable

from navigator.automation.guided_task.models import GuidedFlow, GuidedPlan, GuidedStep
from navigator.automation.record import _slug

_PLAN_PROMPT = """You are helping a SaaS company plan a product demo recording.

The client describes what the demo should cover. Produce ONE reusable demo flow
with ordered steps (not multiple flows). If they listed FLOW 1 / FLOW 2 sections,
merge those into one continuous walkthrough with clear step labels.

Never turn rules, guidelines, bullet policies, or "IMPORTANT" lists into steps.

Step kinds:
- USER_INPUT — pause during live demo to ask the visitor (phone, email, name…).
  Never type real visitor data while recording.
- ACTION — click, open, navigate, create, save (agent performs during guided record).

Reply with JSON only:
{{
  "flows": [
    {{
      "name": "Short demo title",
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
_FLOW_HEADER_RE = re.compile(
    r"(?im)^\s*FLOW\s+(\d+)\s*[—–\-:]\s*(.+?)\s*$"
)
_NUMBERED_STEP_RE = re.compile(r"(?m)^\s*(\d+)\.\s+(.+?)(?=(?:\n\s*\d+\.)|\Z)", re.S)
_RULES_HEADER_RE = re.compile(
    r"(?im)^\s*(IMPORTANT|RULES|GUIDED[- ]?AGENT RULES|NOTES)\b"
)


def _slug_alias(text: str, fallback: str) -> str:
    return _slug(text, fallback)[:40]


def _clean_flow_name(raw: str) -> str:
    """'PHONEBOOK / CONTACT SETUP' → 'Phonebook / Contact Setup'."""
    name = re.sub(r"\s+", " ", (raw or "").strip())
    name = re.sub(r"^(?:FLOW\s*\d+\s*[—–\-:]\s*)", "", name, flags=re.I).strip()
    if not name:
        return "Demo walkthrough"
    keep_upper = {"OTP", "API", "CRM", "SMS", "URL", "UI", "ID"}
    parts: list[str] = []
    for tok in name.replace("/", " / ").split():
        up = tok.upper()
        low = tok.lower()
        if up in keep_upper:
            parts.append(up)
        elif low in {"a", "an", "the", "and", "or", "of", "to", "for", "in", "/"}:
            parts.append(low if parts else tok.capitalize())
        elif tok.isupper() and len(tok) > 1:
            parts.append(tok.capitalize())
        else:
            parts.append(tok[:1].upper() + tok[1:] if tok else tok)
    return " ".join(parts)[:60]


def _demo_title(prompt: str, fallback: str = "Product demo") -> str:
    """First non-empty, non-rules line as a short demo name."""
    for ln in (prompt or "").splitlines():
        s = ln.strip()
        if not s or _RULES_HEADER_RE.match(s) or s.startswith("-"):
            continue
        if _FLOW_HEADER_RE.match(s):
            return _clean_flow_name(_FLOW_HEADER_RE.match(s).group(2))  # type: ignore[union-attr]
        # Skip ultra-long instruction openers; take first ~8 words.
        words = s.split()
        return _clean_flow_name(" ".join(words[:8]))
    return fallback


def _step_from_text(text: str, index: int) -> GuidedStep:
    chunk = re.sub(r"\s+", " ", text.strip())
    label = re.split(r"\s+[-•]\s+", chunk)[0].strip()[:120] or f"Step {index}"
    alias = _slug_alias(label, f"step_{index}")
    if _USER_INPUT_RE.search(chunk):
        return GuidedStep(
            kind="USER_INPUT",
            label=label,
            alias=alias,
            live_question=f"Could you share your {alias.replace('_', ' ')}?",
            spoken=label[:200],
        )
    return GuidedStep(
        kind="ACTION",
        label=label,
        alias=alias,
        spoken=label[:200],
        action_hint=chunk[:200],
    )


def _steps_from_section(body: str) -> list[GuidedStep]:
    numbered = list(_NUMBERED_STEP_RE.finditer(body))
    if numbered:
        return [_step_from_text(m.group(2), i + 1) for i, m in enumerate(numbered)]
    lines = [
        ln.strip(" -•\t")
        for ln in body.splitlines()
        if ln.strip() and not _RULES_HEADER_RE.match(ln)
    ]
    chunks = [ln for ln in lines if len(ln) > 3][:24]
    if not chunks:
        chunks = [body.strip()[:200] or "Walkthrough"]
    return [_step_from_text(c, i + 1) for i, c in enumerate(chunks)]


def _as_single_flow(plan: GuidedPlan, prompt: str) -> GuidedPlan:
    """Phase C: always one GuidedFlow — merge sections into one walkthrough."""
    if len(plan.flows) == 1:
        return plan
    steps: list[GuidedStep] = []
    for fi, flow in enumerate(plan.flows):
        # Keep section context in labels when merging multi-FLOW prompts.
        for si, step in enumerate(flow.steps):
            if fi > 0 and si == 0:
                label = f"{flow.name}: {step.label}"[:120]
                steps.append(
                    GuidedStep(
                        kind=step.kind,
                        label=label,
                        alias=_slug_alias(label, step.alias),
                        live_question=step.live_question,
                        spoken=step.spoken or label,
                        action_hint=step.action_hint,
                    )
                )
            else:
                steps.append(step)
    if not steps:
        steps = [
            GuidedStep(
                kind="ACTION",
                label="Open product",
                alias="open_product",
                spoken="Let's open the product.",
                action_hint="open home",
            )
        ]
    name = _demo_title(prompt, plan.flows[0].name if plan.flows else "Product demo")
    fid = _slug_alias(name, "guided_demo")
    return GuidedPlan(
        task_id=plan.task_id,
        prompt=plan.prompt,
        flows=(
            GuidedFlow(
                name=name,
                flow_id=fid,
                page_id=plan.flows[0].page_id if plan.flows else "dashboard",
                steps=tuple(steps),
            ),
        ),
    )


def _structured_flow_plan(prompt: str) -> GuidedPlan | None:
    """FLOW 1 / FLOW 2… → one merged demo flow (Phase C)."""
    matches = list(_FLOW_HEADER_RE.finditer(prompt))
    if not matches:
        return None

    steps: list[GuidedStep] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(prompt)
        body = prompt[start:end]
        rules = _RULES_HEADER_RE.search(body)
        if rules:
            body = body[: rules.start()]
        section = _clean_flow_name(m.group(2))
        section_steps = _steps_from_section(body)
        for si, step in enumerate(section_steps):
            if si == 0 and i > 0:
                label = f"{section}: {step.label}"[:120]
                steps.append(
                    GuidedStep(
                        kind=step.kind,
                        label=label,
                        alias=_slug_alias(label, step.alias),
                        live_question=step.live_question,
                        spoken=step.spoken or label,
                        action_hint=step.action_hint,
                    )
                )
            else:
                steps.append(step)

    if not steps:
        return None
    name = _demo_title(prompt, "Product demo")
    fid = _slug_alias(name, "guided_demo")
    return GuidedPlan(
        task_id=GuidedPlan.new_id(),
        prompt=prompt.strip(),
        flows=(
            GuidedFlow(
                name=name,
                flow_id=fid,
                page_id="dashboard",
                steps=tuple(steps),
            ),
        ),
    )


def _heuristic_plan(prompt: str) -> GuidedPlan:
    """No LLM — one flow from FLOW headers or sentence chunks."""
    structured = _structured_flow_plan(prompt)
    if structured is not None:
        return structured

    task_id = GuidedPlan.new_id()
    work = prompt
    rules = _RULES_HEADER_RE.search(work)
    if rules:
        work = work[: rules.start()]

    chunks = [
        c.strip()
        for c in re.split(r"[.\n;]+", work)
        if c.strip() and len(c.strip()) > 3 and not c.strip().startswith("-")
    ]
    if not chunks:
        chunks = [prompt.strip() or "demo walkthrough"]

    steps = [_step_from_text(chunk, i + 1) for i, chunk in enumerate(chunks[:40])]
    name = _demo_title(prompt)
    fid = _slug_alias(name, "guided_demo")
    return GuidedPlan(
        task_id=task_id,
        prompt=prompt.strip(),
        flows=(
            GuidedFlow(
                name=name,
                flow_id=fid,
                page_id="dashboard",
                steps=tuple(steps),
            ),
        ),
    )


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
        fname = _clean_flow_name(str(f.get("name") or f"Section {fi + 1}"))
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
    return _as_single_flow(
        GuidedPlan(task_id=GuidedPlan.new_id(), prompt=prompt.strip(), flows=tuple(flows)),
        prompt,
    )


def plan_from_task(
    prompt: str,
    *,
    ask_text: Callable[[str], str] | None = None,
) -> GuidedPlan:
    """Build a guided plan from the Client's task prompt (always one flow)."""
    cleaned = (prompt or "").strip()
    if not cleaned:
        raise ValueError("task prompt is required")

    structured = _structured_flow_plan(cleaned)
    if structured is not None:
        return structured

    if ask_text is not None:
        try:
            raw = ask_text(_PLAN_PROMPT.format(task=cleaned))
            parsed = _parse_llm_plan(raw, cleaned)
            if parsed is not None and parsed.flows:
                return _as_single_flow(parsed, cleaned)
        except Exception as exc:  # noqa: BLE001
            print(f"[guided] LLM plan failed, using heuristic: {exc}", flush=True)

    return _heuristic_plan(cleaned)
