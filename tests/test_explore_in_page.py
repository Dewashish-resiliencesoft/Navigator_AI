"""In-page demo steps and commit recording without execution."""

from __future__ import annotations

from navigator.automation.explore.explorer import ExplorerDeps, _LiveGraph, _step
from navigator.automation.explore.guardrail import GuardrailVerdict
from navigator.automation.explore import reason
from navigator.automation.explore.session import ExplorationSession, fingerprint
from navigator.core.schemas import ToolResult, VerifyResult


class FakePage:
    def __init__(self, url: str, elements: list[dict]) -> None:
        self.url = url
        self.elements = list(elements)

    def evaluate(self, _js: str) -> list[dict]:
        return self.elements


def _el(**kw) -> dict:
    base = {
        "tag": "button", "id": "", "name": "", "testid": "", "text": "",
        "label": "", "aria_label": "", "title": "", "alt": "", "role": "",
        "type": "", "autocomplete": "", "href": "", "value": "", "fillable": False,
    }
    base.update(kw)
    return base


def test_same_path_dom_change_records_demo_step(monkeypatch):
    """Modal open on unchanged path must become a demo step (explorer recording fix)."""
    page = FakePage(
        "https://app.example.com/contacts",
        [_el(testid="open-modal", text="Add contact")],
    )
    executed: list = []

    def _execute(_page, _graph, _page_id, call):
        executed.append(call)
        page.elements = [
            _el(testid="name", text="", fillable=True),
            _el(testid="close", text="Close"),
        ]
        return ToolResult(ok=True, tool=call.tool, detail="ok", duration_ms=1), "main"

    session = ExplorationSession(
        product_id="acme",
        base_url="https://app.example.com",
    )
    session.flow_paths.add("/contacts")
    graph = _LiveGraph(session.base_url)
    deps = ExplorerDeps(
        page=page,
        execute=_execute,
        verify=lambda *_: VerifyResult(passed=True, actual="ok"),
        guard_judge=lambda _p: '{"destructive": false}',
    )
    el = page.elements[0]
    _step(
        session,
        deps,
        graph,
        el,
        page.url,
        reason.Choice(0, "open modal", ""),
        _execute,
        deps.verify,
    )
    assert len(session.steps) == 1
    assert session.steps[0].tool == "click_element"
    assert executed


def test_commit_recorded_not_executed(monkeypatch):
    page = FakePage(
        "https://app.example.com/contacts",
        [_el(testid="save", text="Save contact")],
    )
    executed: list = []

    monkeypatch.setattr(
        "navigator.automation.explore.explorer.classify_action",
        lambda el, judge=None: GuardrailVerdict(flagged=True, reason="submit", source="keyword"),
    )

    session = ExplorationSession(
        product_id="acme",
        base_url="https://app.example.com",
    )
    session.flow_paths.add("/contacts")
    graph = _LiveGraph(session.base_url)
    deps = ExplorerDeps(
        page=page,
        execute=lambda *_: (executed.append(1) or ToolResult(ok=True, tool="x", detail="", duration_ms=1), "main"),
        verify=lambda *_: VerifyResult(passed=True, actual="ok"),
        guard_judge=lambda _p: '{"destructive": false}',
    )
    el = page.elements[0]
    _step(
        session,
        deps,
        graph,
        el,
        page.url,
        reason.Choice(0, "save", "Save it"),
        deps.execute,
        deps.verify,
        planned_kind="commit",
    )
    assert len(session.steps) == 1
    assert session.steps[0].needs_approval is True
    assert not executed
