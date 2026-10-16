"""Guided hands session — ties recorder page to plan progress."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from navigator.automation.guided_task.hands import element_by_index, execute_guided_step
from navigator.automation.guided_task.models import GuidedFlow, GuidedPlan, GuidedStep


@dataclass
class GuidedQuestion:
    qid: str
    alias: str
    prompt: str
    context: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    kind: str = "pick"  # pick | user_input
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
    #: Client paused auto-drive (Phase A).
    client_paused: bool = False
    #: Client took over clicks; ticks no-op until resume (Phase A).
    barged: bool = False
    #: Called with updated GuidedPlan when Ask-visitor rewrites a step.
    on_plan_update: Any = None

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
            "current_step_kind": step.kind if step else None,
            "last_result": self.last_result,
            "log": list(self.log[-40:]),
            "client_paused": self.client_paused,
            "barged": self.barged,
        }
        if self.pending_question and not self.pending_question.resolved:
            q = self.pending_question
            out["question"] = {
                "qid": q.qid,
                "alias": q.alias,
                "prompt": q.prompt,
                "kind": q.kind,
                "context": q.context,
                "candidates": q.candidates,
            }
        return out

    def pause(self) -> dict[str, Any]:
        if not self.active:
            raise RuntimeError("no active guided hands session")
        self.client_paused = True
        self.phase = "paused"
        self.log.append("Paused by Client.")
        return self.status_dict()

    def resume(self) -> dict[str, Any]:
        if not self.active:
            raise RuntimeError("no active guided hands session")
        self.client_paused = False
        self.barged = False
        if self.pending_question and not self.pending_question.resolved:
            self.phase = "awaiting_input"
        else:
            self.phase = "acting"
        self.log.append("Resumed guided hands.")
        return self.status_dict()

    def barge(self) -> dict[str, Any]:
        """Client takes over — recorder keeps capturing; ticks idle until resume."""
        if not self.active:
            raise RuntimeError("no active guided hands session")
        self.barged = True
        self.client_paused = False
        self.phase = "barged"
        self.log.append("Client barge-in — click in the browser; Resume when ready.")
        return self.status_dict()

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
            self.log.append("Guided task complete — walkthrough recorded.")

    def tick(self) -> dict[str, Any]:
        if not self.active or self.page is None:
            return self.status_dict()
        if self.client_paused or self.barged:
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
            kind = str(result.get("reason") or "pick")
            q = GuidedQuestion(
                qid=str(uuid4()),
                alias=str(result.get("alias") or step.alias),
                prompt=str(result.get("prompt") or "Need your help"),
                context=dict(result.get("context") or {}),
                candidates=list(result.get("candidates") or []),
                kind="user_input" if kind == "user_input" else "pick",
            )
            self.pending_question = q
            self.phase = "awaiting_input"
            self.log.append(f"Paused: {q.prompt}")
            return self.status_dict()

        if result.get("ok"):
            msg = result.get("message") or step.label
            self.log.append(f"Action: {msg}")
            self._advance()
            if not self.active:
                self.phase = "done"
        else:
            self.phase = "failed"
            self.log.append(f"Failed: {result.get('error') or 'unknown'}")
        return self.status_dict()

    def mark_ask_visitor(self, qid: str, client_prompt: str) -> dict[str, Any]:
        """Pause only: screenshot + Client prompt → USER_INPUT on current step."""
        q = self.pending_question
        if q is None or q.resolved or q.qid != qid:
            raise RuntimeError("no pending guided question")
        if self.page is None:
            raise RuntimeError("recorder page not available")

        from navigator.automation.guided_task.ask_visitor import propose_live_question
        from navigator.automation.record import _slug

        live_q = propose_live_question(self.page, client_prompt)
        step = self.current_step()
        flow = self.current_flow()
        if step is None or flow is None:
            raise RuntimeError("no current guided step")

        alias = (step.alias or _slug(live_q, "ask_visitor"))[:40]
        new_step = GuidedStep(
            kind="USER_INPUT",
            label=(live_q[:80] or "Ask visitor").strip(),
            alias=alias,
            live_question=live_q,
            spoken=live_q,
            action_hint="",
        )
        steps = list(flow.steps)
        steps[self.step_index] = new_step
        new_flow = GuidedFlow(
            name=flow.name,
            flow_id=flow.flow_id,
            page_id=flow.page_id,
            steps=tuple(steps),
        )
        flows = list(self.plan.flows)
        flows[self.flow_index] = new_flow
        self.plan = GuidedPlan(
            task_id=self.plan.task_id,
            prompt=self.plan.prompt,
            flows=tuple(flows),
        )
        self.log.append(f"Ask visitor: {live_q}")
        if callable(self.on_plan_update):
            try:
                self.on_plan_update(self.plan)
            except Exception as exc:  # noqa: BLE001
                self.log.append(f"Plan persist failed: {exc}")

        q.answer = live_q
        q.resolved = True
        self.pending_question = None
        self._advance()
        self.phase = "acting" if self.active else "done"
        self.client_paused = False
        self.barged = False
        return self.status_dict()

    def answer(
        self,
        qid: str,
        *,
        candidate_index: int | None = None,
        value: str | None = None,
        skip: bool = False,
    ) -> dict[str, Any]:
        q = self.pending_question
        if q is None or q.resolved or q.qid != qid:
            raise RuntimeError("no pending guided question")
        if self.page is None:
            raise RuntimeError("recorder page not available")

        if q.kind == "user_input":
            # Recording checkpoint: never type visitor data; mark step done.
            q.answer = (value or "").strip() or None
            q.resolved = True
            self.pending_question = None
            if skip or not q.answer:
                self.log.append(f"USER_INPUT checkpoint noted (no fill): {q.alias}")
            else:
                self.log.append(
                    f"USER_INPUT checkpoint saved as live ask ({q.alias}) — not typed."
                )
            self._advance()
            self.phase = "acting" if self.active else "done"
            return self.status_dict()

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


def start_guided_hands(
    plan: GuidedPlan,
    page: Any,
    *,
    flow_index: int = 0,
    on_plan_update: Any = None,
) -> GuidedHandsSession:
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
            on_plan_update=on_plan_update,
        )
        _session.log.append(f"Started guided hands — {len(plan.flows)} flow(s).")
        return _session


def stop_guided_hands() -> None:
    global _session
    with _lock:
        if _session is not None:
            _session.active = False
            _session.phase = "stopped"
        _session = None


def poll_hands_commands(page: Any, commands: list) -> None:
    """Called from record_session loop — drain queued hands ticks.

    Runs in the recorder process/thread (incl. Playwright WS worker), so
    ``hands_start`` must build the session here — not in the uvicorn process.
    """
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
        if action == "hands_start":
            from navigator.automation.guided_task.models import GuidedPlan

            plan = GuidedPlan.from_meta(cmd.get("plan") or {})
            if plan is None or not plan.flows:
                print("[guided-hands] hands_start missing plan", flush=True)
                continue
            try:
                fi = int(cmd.get("flow_index") or 0)
            except (TypeError, ValueError):
                fi = 0

            def _on_plan(p: GuidedPlan) -> None:
                meta = p.to_meta()
                # Gate / mp ns may be attached via page-less side channel later.
                sink = getattr(page, "_nav_plan_sink", None)
                if callable(sink):
                    sink(meta)

            try:
                # Replace any stale session from a prior attempt.
                stop_guided_hands()
                start_guided_hands(
                    plan, page, flow_index=max(0, fi), on_plan_update=_on_plan
                )
            except RuntimeError as exc:
                print(f"[guided-hands] start failed: {exc}", flush=True)
            sess = get_guided_hands_session()
            continue
        if action == "mark_ask" and sess is not None:
            qid = str(cmd.get("qid") or "")
            prompt = str(cmd.get("prompt") or "")
            try:
                sess.mark_ask_visitor(qid, prompt)
            except RuntimeError as exc:
                sess.log.append(f"Ask visitor failed: {exc}")
            continue
        if action == "tick" and sess is not None and sess.active:
            sess.tick()
        elif action == "pause" and sess is not None:
            try:
                sess.pause()
            except RuntimeError as exc:
                sess.log.append(f"Pause failed: {exc}")
        elif action == "resume" and sess is not None:
            try:
                sess.resume()
            except RuntimeError as exc:
                sess.log.append(f"Resume failed: {exc}")
        elif action == "barge" and sess is not None:
            try:
                sess.barge()
            except RuntimeError as exc:
                sess.log.append(f"Barge failed: {exc}")
        elif action == "answer" and sess is not None:
            qid = str(cmd.get("qid") or "")
            idx = cmd.get("candidate_index")
            try:
                ci = int(idx) if idx is not None else None
            except (TypeError, ValueError):
                ci = None
            try:
                sess.answer(
                    qid,
                    candidate_index=ci,
                    value=str(cmd.get("value") or "") or None,
                    skip=bool(cmd.get("skip")),
                )
            except RuntimeError as exc:
                sess.log.append(f"Answer failed: {exc}")
        elif action == "stop":
            stop_guided_hands()
            sess = None
        time.sleep(0.05)
        sess = get_guided_hands_session()
        if sess is not None and sess.page is None:
            sess.page = page
