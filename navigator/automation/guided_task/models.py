"""Guided Agent plan shapes — Client dashboard only, draft site graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

GuidedStepKind = Literal["USER_INPUT", "ACTION"]


@dataclass(frozen=True)
class GuidedStep:
    kind: GuidedStepKind
    label: str
    alias: str
    live_question: str | None = None
    spoken: str = ""
    action_hint: str = ""


@dataclass(frozen=True)
class GuidedFlow:
    name: str
    flow_id: str
    page_id: str
    steps: tuple[GuidedStep, ...] = field(default_factory=tuple)


@dataclass
class GuidedPlan:
    task_id: str
    prompt: str
    flows: tuple[GuidedFlow, ...] = field(default_factory=tuple)

    @staticmethod
    def new_id() -> str:
        return f"gt_{uuid4().hex[:12]}"

    def to_meta(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "flows": [
                {
                    "name": f.name,
                    "flow_id": f.flow_id,
                    "page_id": f.page_id,
                    "steps": [
                        {
                            "kind": s.kind,
                            "label": s.label,
                            "alias": s.alias,
                            "live_question": s.live_question,
                            "spoken": s.spoken,
                            "action_hint": s.action_hint,
                        }
                        for s in f.steps
                    ],
                }
                for f in self.flows
            ],
        }

    @classmethod
    def from_meta(cls, raw: dict[str, Any]) -> GuidedPlan | None:
        if not isinstance(raw, dict):
            return None
        task_id = str(raw.get("task_id") or "").strip()
        if not task_id:
            return None
        flows: list[GuidedFlow] = []
        for f in raw.get("flows") or []:
            if not isinstance(f, dict):
                continue
            fid = str(f.get("flow_id") or "").strip()
            if not fid:
                continue
            steps: list[GuidedStep] = []
            for s in f.get("steps") or []:
                if not isinstance(s, dict):
                    continue
                kind = str(s.get("kind") or "ACTION").strip().upper()
                if kind not in {"USER_INPUT", "ACTION"}:
                    kind = "ACTION"
                alias = str(s.get("alias") or "").strip()
                if not alias:
                    continue
                steps.append(
                    GuidedStep(
                        kind=kind,  # type: ignore[arg-type]
                        label=str(s.get("label") or alias).strip(),
                        alias=alias,
                        live_question=(
                            str(s.get("live_question")).strip()
                            if s.get("live_question")
                            else None
                        ),
                        spoken=str(s.get("spoken") or "").strip(),
                        action_hint=str(s.get("action_hint") or "").strip(),
                    )
                )
            flows.append(
                GuidedFlow(
                    name=str(f.get("name") or fid).strip(),
                    flow_id=fid,
                    page_id=str(f.get("page_id") or "dashboard").strip() or "dashboard",
                    steps=tuple(steps),
                )
            )
        return cls(
            task_id=task_id,
            prompt=str(raw.get("prompt") or "").strip(),
            flows=tuple(flows),
        )
