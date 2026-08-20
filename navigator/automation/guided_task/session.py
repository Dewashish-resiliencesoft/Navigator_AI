"""Guided hands session — ties recorder page to plan progress."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from navigator.automation.guided_task.hands import element_by_index, execute_guided_step
from navigator.automation.guided_task.models import GuidedFlow, GuidedPlan


@dataclass
class GuidedQuestion:
    qid: str
    alias: str
    prompt: str
    context: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    answer: str | None = None
    candidate_index: int | None = None
    resolved: bool = False


@dataclass
class GuidedHandsSession:
    plan: GuidedPlan
    flow_index: int = 0
    step_index: int = 0
    active: bool = False
    phase: str = "idle"
    last_result: dict[str, Any] = field(default_factory=dict)
    pending_question: GuidedQuestion | None = None
    log: list[str] = field(default_factory=list)
    page: Any = None

    def current_flow(self) -> GuidedFlow | None:
        if 0 <= self.flow_index < len(self.plan.flows):
            return self.plan.flows[self.flow_index]
        return None

    def current_step(self):
        flow = self.current_flow()
        if flow is None:
            return None
        if 0 <= self.step_index < len(flow.steps):
            return flow.steps[self.step_index]
        return None

    def progress(self) -> dict[str, int]:
        total_steps = sum(len(f.steps) for f in self.plan.flows)
        done = 0
        for fi, f in enumerate(self.plan.flows):
            for si in range(len(f.steps)):
                if fi < self.flow_index or (fi == self.flow_index and si < self.step_index):
                    done += 1
        return {
            "flows_total": len(self.plan.flows),
            "flows_done": min(self.flow_index, len(self.plan.flows)),
            "steps_total": total_steps,
            "steps_done": done,
            "flow_index": self.flow_index,
            "step_index": self.step_index,
        }

    def status_dict(self) -> dict[str, Any]:
        flow = self.current_flow()
        step = self.current_step()
        out: dict[str, Any] = {
            "active": self.active,
            "phase": self.phase,
            "progress": self.progress(),
            "current_flow": flow.name if flow else None,
            "current_flow_id": flow.flow_id if flow else None,
            "current_step": step.label if step else None,
            "last_result": self.last_result,
            "log": list(self.log[-40:]),
        }
        if self.pending_question and not self.pending_question.resolved:
            q = self.pending_question
            out["question"] = {
                "qid": q.qid,
                "alias": q.alias,
                "prompt": q.prompt,
                "context": q.context,
                "candidates": q.candidates,
            }
        return out

    def _advance(self) -> None:
        flow = self.current_flow()
        if flow is None:
            self.active = False
            self.phase = "done"
            return
        self.step_index += 1
        if self.step_index >= len(flow.steps):
            self.flow_index += 1
            self.step_index = 0
        if self.flow_index >= len(self.plan.flows):
            self.active = False
            self.phase = "done"
            self.log.append("Guided task complete — all sections walked.")

    def tick(self) -> dict[str, Any]:
        if not self.active or self.page is None:
            return self.status_dict()
        if self.pending_question and not self.pending_question.resolved:
            return self.status_dict()

        step = self.current_step()
        if step is None:
            self.active = False
            self.phase = "done"
            return self.status_dict()

        self.phase = "acting"
        result = execute_guided_step(self.page, step)
        self.last_result = result

        if result.get("paused"):
            q = GuidedQuestion(
                qid=str(uuid4()),
                alias=str(result.get("alias") or step.alias),
                prompt=str(result.get("prompt") or "Need your help"),
                context=dict(result.get("context") or {}),
                candidates=list(result.get("candidates") or []),
            )
            self.pending_question = q
            self.phase = "awaiting_input"
            self.log.append(f"Paused: {q.prompt}")
            return self.status_dict()

        if result.get("ok"):
            msg = result.get("message") or step.label
            if result.get("skipped"):
                self.log.append(f"Checkpoint: {msg}")
            else:
                self.log.append(f"Action: {msg}")
            self._advance()
            if not self.active:
                self.phase = "done"
        else:
            self.phase = "failed"
            self.log.append(f"Failed: {result.get('error') or 'unknown'}")
        return self.status_dict()

    def answer(self, qid: str, *, candidate_index: int | None = None) -> dict[str, Any]:
        q = self.pending_question
        if q is None or q.resolved or q.qid != qid:
            raise RuntimeError("no pending guided question")
        if self.page is None:
            raise RuntimeError("recorder page not available")

        from navigator.automation.explore.perceive import inventory
        from navigator.automation.browser.cursor import click_with_cursor
        from navigator.automation.record import prefer_selector

        if candidate_index is not None:
            el = element_by_index(inventory(self.page), candidate_index)
            if el is None:
                raise RuntimeError("invalid candidate index")
            alias, css = prefer_selector(el)
            click_with_cursor(self.page, css)
            self.log.append(f"Client picked: {alias}")
            self.last_result = {"ok": True, "alias": alias, "selector": css}

        q.resolved = True
        self.pending_question = None
        self._advance()
        self.phase = "acting" if self.active else "done"
        return self.status_dict()


_lock = threading.Lock()
_session: GuidedHandsSession | None = None


def get_guided_hands_session() -> GuidedHandsSession | None:
    with _lock:
        return _session


def start_guided_hands(plan: GuidedPlan, page: Any, *, flow_index: int = 0) -> GuidedHandsSession:
    global _session
    with _lock:
        if _session is not None and _session.active:
            raise RuntimeError("guided hands session already running")
        _session = GuidedHandsSession(
            plan=plan,
            flow_index=flow_index,
            step_index=0,
            active=True,
            phase="acting",
            page=page,
        )
        _session.log.append(f"Started guided hands — {len(plan.flows)} flows.")
        return _session


def stop_guided_hands() -> None:
    global _session
    with _lock:
        if _session is not None:
            _session.active = False
            _session.phase = "stopped"
        _session = None


def poll_hands_commands(page: Any, commands: list) -> None:
    """Called from record_session loop — drain queued hands ticks."""
    sess = get_guided_hands_session()
    if sess is not None and sess.page is None:
        sess.page = page

    if not commands:
        return
    while commands:
        cmd = commands.pop(0)
        if not isinstance(cmd, dict):
            continue
        action = str(cmd.get("action") or "").strip()
        if action == "tick" and sess is not None and sess.active:
            sess.tick()
        elif action == "answer" and sess is not None:
            qid = str(cmd.get("qid") or "")
            idx = cmd.get("candidate_index")
            try:
                ci = int(idx) if idx is not None else None
            except (TypeError, ValueError):
                ci = None
            try:
                sess.answer(qid, candidate_index=ci)
            except RuntimeError as exc:
                sess.log.append(f"Answer failed: {exc}")
        elif action == "stop":
            stop_guided_hands()
        time.sleep(0.05)
