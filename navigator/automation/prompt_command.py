"""Prompt command channel: markers, strip, structured AgentTask (record → live).

Markers ``prompt start`` / ``prompt stop`` are control signals — never narration.
Parsing produces typed steps (v1: ask_user, save_variable, fill_field, use_variable).
Execution happens at live demo time, not while recording.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

PromptMode = Literal["narration", "prompt_listening", "preview"]

MARKER_START = "prompt start"
MARKER_STOP = "prompt stop"

_Op = Literal["ask_user", "save_variable", "fill_field", "use_variable"]

_MARKER_RE = re.compile(
    r"\bprompt\s+start\b|\bprompt\s+stop\b",
    re.IGNORECASE,
)


@dataclass
class AgentTaskStep:
    op: _Op
    variable: str = ""
    question: str = ""
    selector: str = ""
    step_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"op": self.op}
        if self.variable:
            d["variable"] = self.variable
        if self.question:
            d["question"] = self.question
        if self.selector:
            d["selector"] = self.selector
        if self.step_index is not None:
            d["step_index"] = self.step_index
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AgentTaskStep:
        op = str(raw.get("op") or "").strip()
        if op not in {"ask_user", "save_variable", "fill_field", "use_variable"}:
            raise ValueError(f"unsupported op {op!r}")
        idx = raw.get("step_index")
        return cls(
            op=op,  # type: ignore[arg-type]
            variable=str(raw.get("variable") or "").strip(),
            question=str(raw.get("question") or "").strip(),
            selector=str(raw.get("selector") or "").strip(),
            step_index=int(idx) if idx is not None and str(idx).strip() != "" else None,
        )


@dataclass
class AgentTask:
    id: str
    raw_instruction: str
    steps: list[AgentTaskStep] = field(default_factory=list)
    flow_id: str = ""
    step_index: int | None = None
    selector: str = ""
    status: Literal["draft", "confirmed"] = "draft"
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "agent_task",
            "id": self.id,
            "raw_instruction": self.raw_instruction,
            "steps": [s.to_dict() for s in self.steps],
            "flow_id": self.flow_id,
            "step_index": self.step_index,
            "selector": self.selector,
            "status": self.status,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AgentTask:
        steps_raw = raw.get("steps") or []
        steps = [
            AgentTaskStep.from_dict(s) for s in steps_raw if isinstance(s, dict)
        ]
        idx = raw.get("step_index")
        return cls(
            id=str(raw.get("id") or uuid4().hex[:12]),
            raw_instruction=str(raw.get("raw_instruction") or "").strip(),
            steps=steps,
            flow_id=str(raw.get("flow_id") or "").strip(),
            step_index=int(idx) if idx is not None and str(idx).strip() != "" else None,
            selector=str(raw.get("selector") or "").strip(),
            status="confirmed" if raw.get("status") == "confirmed" else "draft",
            summary=str(raw.get("summary") or "").strip(),
        )


def normalize_utterance(text: str) -> str:
    return " ".join((text or "").lower().split())


def is_marker_start(text: str) -> bool:
    return normalize_utterance(text) == MARKER_START


def is_marker_stop(text: str) -> bool:
    return normalize_utterance(text) == MARKER_STOP


def strip_prompt_markers(text: str) -> str:
    """Remove prompt start/stop phrases so they never become narration."""
    if not text:
        return ""
    cleaned = _MARKER_RE.sub(" ", text)
    return " ".join(cleaned.split()).strip()


def detect_marker_in_text(text: str) -> Literal["start", "stop", ""]:
    """Scan free text for an exact marker phrase (order: start then stop)."""
    n = normalize_utterance(text)
    if MARKER_START in n:
        return "start"
    if MARKER_STOP in n:
        return "stop"
    return ""


def _slug_var(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s or "value"


def heuristic_parse_instruction(
    instruction: str,
    *,
    current_field: dict[str, Any] | None = None,
) -> AgentTask:
    """Deterministic fallback when Gemini is unavailable.

    Looks for ask/save/fill/use intent in plain English.
    """
    raw = (instruction or "").strip()
    low = raw.lower()
    field = current_field if isinstance(current_field, dict) else {}
    alias = _slug_var(
        str(field.get("alias") or field.get("variable") or "phone_number")
    )
    selector = str(field.get("selector") or "").strip()
    step_index = field.get("step_index")
    try:
        step_i = int(step_index) if step_index is not None else None
    except (TypeError, ValueError):
        step_i = None

    steps: list[AgentTaskStep] = []
    # Variable name: "save it as X" / "remember as X"
    m = re.search(
        r"(?:save|remember|store)\s+(?:it\s+)?(?:as|to)\s+([a-zA-Z][\w]*)",
        raw,
        re.I,
    )
    variable = _slug_var(m.group(1)) if m else alias

    asks = any(
        k in low
        for k in ("ask", "visitor", "end user", "user for", "phone", "email", "name")
    )
    fills = any(k in low for k in ("fill", "type into", "put in", "enter into", "current field"))
    uses = any(k in low for k in ("use the saved", "reuse", "saved contact", "that number"))

    if asks or (not fills and not uses):
        q = raw
        if len(q) > 160:
            q = f"What is your {variable.replace('_', ' ')}?"
        steps.append(
            AgentTaskStep(op="ask_user", variable=variable, question=q)
        )
        steps.append(AgentTaskStep(op="save_variable", variable=variable))
    if fills or (field and asks):
        steps.append(
            AgentTaskStep(
                op="fill_field",
                variable=variable,
                selector=selector,
                step_index=step_i,
            )
        )
    if uses and not any(s.op == "use_variable" for s in steps):
        steps.append(AgentTaskStep(op="use_variable", variable=variable))

    if not steps:
        steps.append(
            AgentTaskStep(
                op="ask_user",
                variable=variable,
                question=raw or f"What is your {variable.replace('_', ' ')}?",
            )
        )

    summary_bits = []
    for s in steps:
        if s.op == "ask_user":
            summary_bits.append(f"Ask visitor → {s.variable}")
        elif s.op == "save_variable":
            summary_bits.append(f"Save → {s.variable}")
        elif s.op == "fill_field":
            summary_bits.append("Fill → current field" if not s.selector else f"Fill → {s.selector}")
        elif s.op == "use_variable":
            summary_bits.append(f"Use → {s.variable}")

    return AgentTask(
        id=uuid4().hex[:12],
        raw_instruction=raw,
        steps=steps,
        step_index=step_i,
        selector=selector,
        status="draft",
        summary=" · ".join(summary_bits),
    )


def parse_agent_task_instruction(
    instruction: str,
    *,
    current_field: dict[str, Any] | None = None,
    use_llm: bool = True,
) -> AgentTask:
    """Translate Client instruction → AgentTask. LLM optional; heuristic always works."""
    raw = (instruction or "").strip()
    if not raw:
        raise ValueError("empty prompt instruction")

    if use_llm:
        try:
            task = _llm_parse(raw, current_field=current_field)
            if task.steps:
                return task
        except Exception as exc:  # noqa: BLE001
            print(f"[prompt-command] LLM parse failed, heuristic: {exc}", flush=True)

    return heuristic_parse_instruction(raw, current_field=current_field)


def _llm_parse(
    instruction: str,
    *,
    current_field: dict[str, Any] | None,
) -> AgentTask:
    from navigator.agent.providers import get_provider

    field = current_field if isinstance(current_field, dict) else {}
    schema_hint = {
        "steps": [
            {
                "op": "ask_user|save_variable|fill_field|use_variable",
                "variable": "snake_case",
                "question": "optional",
                "selector": "optional",
                "step_index": "optional int",
            }
        ],
        "summary": "short human summary",
    }
    system = (
        "Convert a Client demo-authoring instruction into JSON for an AgentTask. "
        "Only ops: ask_user, save_variable, fill_field, use_variable. "
        "Respond with JSON only."
    )
    user = (
        f"Current field: {json.dumps(field, default=str)}\n"
        f"Instruction:\n{instruction}\n"
        f"Schema hint: {json.dumps(schema_hint)}"
    )
    provider = get_provider()
    text = provider.complete(system, user)
    blob = (text or "").strip()
    if "```" in blob:
        blob = blob.split("```")[1]
        if blob.startswith("json"):
            blob = blob[4:]
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON in LLM response")
    data = json.loads(blob[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM JSON not an object")
    data["raw_instruction"] = instruction
    data["id"] = uuid4().hex[:12]
    data["status"] = "draft"
    if field.get("selector") and not data.get("selector"):
        data["selector"] = field.get("selector")
    if field.get("step_index") is not None and data.get("step_index") is None:
        data["step_index"] = field.get("step_index")
    task = AgentTask.from_dict(data)
    if not task.summary:
        task.summary = " · ".join(
            f"{s.op}:{s.variable}" for s in task.steps if s.variable
        )
    return task


def agent_tasks_to_meta(tasks: list[AgentTask]) -> list[dict[str, Any]]:
    return [t.to_dict() for t in tasks]


def merge_agent_tasks_into_meta(
    meta: dict[str, Any],
    tasks: list[AgentTask],
) -> dict[str, Any]:
    """Append confirmed tasks into site-graph ``_meta.agent_tasks``."""
    out = dict(meta or {})
    existing = out.get("agent_tasks")
    rows: list[dict[str, Any]] = []
    if isinstance(existing, list):
        rows = [r for r in existing if isinstance(r, dict)]
    by_id = {str(r.get("id")): r for r in rows if r.get("id")}
    for t in tasks:
        by_id[t.id] = t.to_dict()
    out["agent_tasks"] = list(by_id.values())
    return out
