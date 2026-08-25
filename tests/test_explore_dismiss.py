"""Close/dismiss clicks should pass when the control vanishes."""

from __future__ import annotations

from navigator.automation.explore.explorer import ExplorerDeps, explore
from navigator.automation.explore.repair import click_postcondition, looks_like_dismiss
from navigator.automation.explore.session import ExplorationBudget
from navigator.core.schemas import Postcondition, ToolResult, VerifyResult
from test_explore import FakePage, _el, _session


def test_looks_like_dismiss_close():
    assert looks_like_dismiss(_el(text="Close", tag="button"))
    assert not looks_like_dismiss(_el(text="Billing", tag="a", href="/b"))


def test_close_uses_hidden_postcondition():
    pc = click_postcondition("close", _el(text="Close", tag="button"))
    assert pc.check == "hidden"


def test_close_click_passes_when_element_gone():
    close = _el(testid="modal-close", text="Close", tag="button")
    page = FakePage("https://app.example.com/", [close])

    def execute(_p, _graph, _page_id, call):
        page.elements = [_el(testid="main", text="Dashboard")]
        return ToolResult(ok=True, tool=call.tool, detail="clicked", duration_ms=1), "main"

    def verify(_p, _graph, _page_id, expects: Postcondition):
        if expects.check == "hidden":
            return VerifyResult(passed=True, actual="hidden")
        return VerifyResult(passed=False, actual="#modal-close not found")

    session = _session(budget=ExplorationBudget(max_steps=1, max_pages=2))
    explore(
        session,
        ExplorerDeps(
            page=page,
            execute=execute,
            verify=verify,
            guard_judge=lambda _p: '{"destructive": false}',
        ),
    )
    assert not any(
        "repairs exhausted" in str(e.get("msg", ""))
        for e in session.events
        if e.get("type") == "log"
    )
