"""Eval runner implementation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from navigator.agent.brain_router import route_turn


@dataclass(frozen=True)
class EvalCase:
    utterance: str
    expect: str
    flow_id: str | None = None


@dataclass(frozen=True)
class EvalReport:
    total: int
    passed: int
    failed: list[str]

    @property
    def score_pct(self) -> float:
        return 100.0 * self.passed / self.total if self.total else 0.0


def load_cases(path: Path) -> list[EvalCase]:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, list):
        raise ValueError("eval file must be a list of cases")
    out: list[EvalCase] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        out.append(
            EvalCase(
                utterance=str(row.get("utterance") or ""),
                expect=str(row.get("expect") or ""),
                flow_id=(str(row["flow_id"]) if row.get("flow_id") else None),
            )
        )
    return out


def run_eval(
    cases: list[EvalCase],
    *,
    graph,
    page_id: str,
    product_id: str,
    retrieve,
    flow_texts: dict[str, str],
) -> EvalReport:
    failed: list[str] = []
    passed = 0
    for case in cases:
        decision = route_turn(
            utterance=case.utterance,
            phase="walkthrough",
            graph=graph,
            page_id=page_id,
            product_id=product_id,
            flow_texts=flow_texts,
            retrieve=retrieve,
        )
        ok = _matches(case, decision.intent, decision.flow_id, decision.branch)
        if ok:
            passed += 1
        else:
            failed.append(
                f"{case.utterance!r} expected {case.expect} got "
                f"{decision.intent}/{decision.flow_id}/{decision.branch}"
            )
    return EvalReport(total=len(cases), passed=passed, failed=failed)


def _matches(case: EvalCase, intent: str, flow_id: str | None, branch: str) -> bool:
    exp = case.expect.lower()
    if exp == "handoff":
        return intent == "handoff" or branch == "handoff"
    if exp == "knowledge":
        return intent == "answer" or branch == "knowledge_only"
    if exp == "continue":
        return intent == "continue"
    if case.flow_id:
        return flow_id == case.flow_id
    return flow_id == exp or (intent == "run_flow" and flow_id == exp)
