"""Explore skips off-product links — no demo step, no frame."""

from __future__ import annotations

from navigator.automation.explore.explorer import ExplorerDeps, explore
from navigator.automation.explore.session import ExplorationBudget
from navigator.core.schemas import ToolResult, VerifyResult
from tests.test_explore import FakePage, _el, _session


def test_external_link_not_added_to_demo_steps():
    ext = _el(
        testid="help",
        text="Help",
        tag="a",
        href="https://help.other.com/start",
    )
    inner = _el(testid="billing", text="Billing", tag="a", href="/billing")
    page = FakePage("https://app.example.com/", [ext, inner])

    def execute(_p, _graph, _page_id, call):
        page.url = "https://help.other.com/start"
        return ToolResult(ok=True, tool=call.tool, detail="ok", duration_ms=1), "main"

    session = _session(budget=ExplorationBudget(max_steps=2, max_pages=3))
    explore(
        session,
        ExplorerDeps(
            page=page,
            execute=execute,
            verify=lambda *_a, **_k: VerifyResult(passed=True, actual="ok"),
            guard_judge=lambda _p: '{"destructive": false}',
        ),
    )

    assert not session.steps, "external link must not become a demo step"
    msgs = " ".join(
        str(e.get("msg", "")) for e in session.events if e.get("type") == "log"
    )
    assert "external" in msgs.lower()
