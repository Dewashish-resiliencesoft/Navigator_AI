"""Autonomous exploration: the four safety properties that must hold.

Every test drives the real `explore()` loop with a fake page, so what is under
test is the shipped control flow -- not a re-implementation of it.
"""

from __future__ import annotations

import threading
from uuid import uuid4

import pytest

from navigator.automation.explore.explorer import ExplorerDeps, explore
from navigator.automation.explore.fields import classify_field
from navigator.automation.explore.guardrail import classify_action
from navigator.automation.explore.runner import log_failure
from navigator.automation.explore.session import (
    ExplorationBudget,
    ExplorationSession,
    fingerprint,
)
from navigator.core.schemas import ToolResult, VerifyResult


class FakePage:
    """Enough of a Playwright Page for the loop: a URL and an element list."""

    def __init__(self, url: str, elements: list[dict]) -> None:
        self.url = url
        self.elements = elements

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


def _session(**kw) -> ExplorationSession:
    kw.setdefault("product_id", "acme")
    kw.setdefault("base_url", "https://app.example.com")
    return ExplorationSession(**kw)


def _deps(page, *, executed: list, **kw) -> ExplorerDeps:
    def _execute(_page, _graph, _page_id, call):
        executed.append(call)
        return ToolResult(ok=True, tool=call.tool, detail="ok", duration_ms=1), "main"

    def _verify(_page, _graph, _page_id, _expects):
        return VerifyResult(passed=True, actual="ok")

    kw.setdefault("execute", _execute)
    kw.setdefault("verify", _verify)
    return ExplorerDeps(page=page, **kw)


# -- (a) a flagged action is never executed, even when REASON picks it --------


def test_guardrail_blocks_action_the_reasoner_chose(monkeypatch):
    """The reasoning step is told to pick the delete button. It still never runs.

    Enforcement lives in the executor, not the prompt -- a compromised or
    hallucinating model cannot talk its way past it.
    """
    page = FakePage(
        "https://app.example.com/records",
        [_el(testid="delete-workspace", text="Delete workspace")],
    )
    executed: list = []
    session = _session(budget=ExplorationBudget(max_steps=1, max_pages=1))

    # A "compromised" reasoner that always picks index 0 -- the destructive one.
    monkeypatch.setattr(
        "navigator.automation.explore.reason.choose_next",
        lambda **kw: __import__(
            "navigator.automation.explore.reason", fromlist=["Choice"]
        ).Choice(0, "malicious: delete everything", ""),
    )
    # A judge that also says the action is safe. Keyword layer must still hold.
    explore(session, _deps(page, executed=executed, guard_judge=lambda _p: '{"destructive": false}'))

    assert executed == [], "a flagged element must never reach the tool layer"
    assert session.steps == []
    assert [f.label for f in session.flagged] == ["Delete workspace"]
    assert session.flagged[0].source == "keyword"


def test_guardrail_fails_closed_without_a_judge():
    el = _el(testid="export-csv", text="Export report")
    assert classify_action(el, judge=None).flagged
    assert classify_action(el, judge=None).source == "fail_closed"

    def _broken(_prompt: str) -> str:
        raise RuntimeError("groq down")

    assert classify_action(el, judge=_broken).flagged

    safe = classify_action(el, judge=lambda _p: '{"destructive": false, "reason": "read-only"}')
    assert not safe.flagged


# -- (b) a business-specific field pauses instead of guessing -----------------


def test_business_specific_field_waits_for_the_client():
    page = FakePage(
        "https://app.example.com/billing",
        [_el(tag="input", id="gl-code", name="gl_code", label="GL account code", fillable=True)],
    )
    executed: list = []
    session = _session(
        budget=ExplorationBudget(max_steps=1, max_pages=1, answer_timeout_s=5.0)
    )

    def _answer_when_asked() -> None:
        for _ in range(200):
            q = session.pending_question
            if q is not None:
                session.answer(q.qid, "4100-OPEX")
                return
            threading.Event().wait(0.01)

    responder = threading.Thread(target=_answer_when_asked, daemon=True)
    responder.start()
    explore(
        session,
        _deps(
            page,
            executed=executed,
            guard_judge=lambda _p: '{"destructive": false}',
            field_judge=lambda _p: '{"classification": "business_specific", "reason": "internal code"}',
        ),
    )
    responder.join(timeout=5)

    assert len(executed) == 1
    assert executed[0].value == "4100-OPEX", "must use the client's answer, not a guess"
    decision = session.field_decisions[0]
    assert decision.classification == "business_specific"
    assert decision.answered_by == "client"
    assert any(e.get("type") == "question" for e in session.events)


def test_unanswered_business_field_is_skipped_not_guessed():
    page = FakePage(
        "https://app.example.com/billing",
        [_el(tag="input", id="tax-rate", label="Regional tax rate", fillable=True)],
    )
    executed: list = []
    session = _session(
        budget=ExplorationBudget(max_steps=1, max_pages=1, answer_timeout_s=0.05)
    )
    explore(
        session,
        _deps(
            page,
            executed=executed,
            guard_judge=lambda _p: '{"destructive": false}',
            field_judge=lambda _p: '{"classification": "business_specific", "reason": "domain value"}',
        ),
    )
    assert executed == []
    assert session.field_decisions[0].answered_by == "skipped_timeout"


def test_guessable_field_is_filled_without_asking():
    plan = classify_field(_el(tag="input", type="email", name="email", fillable=True), judge=None)
    assert plan.classification == "guessable_safe"
    assert "@" in plan.value


# -- (c) failures land in the same per-product pipeline as live-call failures --


def test_exploration_failures_use_the_live_call_log_pipeline(log):
    from navigator.automation.record import RecordedStep

    session = _session(product_id="acme")
    step = RecordedStep(
        tool="click_element", alias="reports_tab", selector='[data-testid="reports"]',
        postcondition={"check": "visible", "selector": "reports_tab"},
    )
    log_failure(
        log,
        session=session,
        step=step,
        result=ToolResult(ok=False, tool="click_element", detail="timeout", duration_ms=1),
        verify_result=None,
    )

    failures = log.product_failures("acme")
    assert len(failures) == 1
    entry = failures[0]
    assert entry.product_id == "acme"
    assert entry.session_id == session.session_id
    assert entry.tool_call.selector == "reports_tab"
    # Other products cannot see it -- same tenant scoping as a live call.
    assert log.product_failures("other") == []


def test_exploration_failures_are_retrievable_on_a_later_run(tmp_path):
    """A second run reads back what the first run learned, via prior_corrections."""
    pytest.importorskip("chromadb")
    from navigator.automation.explore.runner import prior_corrections
    from navigator.knowledge.memory.seed import seed_correction

    seed_correction(
        tmp_path,
        product_id="acme",
        rule="The reports tab times out; skip it.",
        page="main",
        tool_call_type="click_element",
        source_call_id=str(uuid4()),
    )
    rules = prior_corrections("acme", path=tmp_path)
    assert any("reports tab" in r for r in rules)
    assert prior_corrections("other-tenant", path=tmp_path) == ()


# -- (d) cyclic navigation terminates ----------------------------------------


def test_paginated_loop_terminates():
    """A list whose "next page" link returns identical structure must not loop.

    The fingerprint strips query/fragment, so `?page=2` and `?page=3` collapse to
    one state; once its elements are all tried, the no-new counter ends the run.
    """
    elements = [
        _el(testid="next-page", text="Next"),
        _el(testid="prev-page", text="Previous"),
    ]
    page = FakePage("https://app.example.com/list?page=1", elements)
    executed: list = []
    # Generous bounds: if the loop only stops on a budget, this test is useless.
    session = _session(
        budget=ExplorationBudget(max_pages=500, max_steps=500, max_wall_clock_s=30.0)
    )

    def _execute(_page, _graph, _page_id, call):
        executed.append(call)
        page.url = f"https://app.example.com/list?page={len(executed) + 1}"
        return ToolResult(ok=True, tool=call.tool, detail="ok", duration_ms=1), "main"

    explore(
        session,
        _deps(
            page,
            executed=executed,
            execute=_execute,
            guard_judge=lambda _p: '{"destructive": false}',
        ),
    )

    assert len(session.visited) == 1, "query-only URL changes are not new states"
    assert len(executed) == 2, "each element tried once, then the run ends"


def test_fingerprint_ignores_query_but_tracks_dom_change():
    a = fingerprint("https://x.test/list?page=1", [_el(text="Next")])
    b = fingerprint("https://x.test/list?page=9", [_el(text="Next")])
    c = fingerprint("https://x.test/list", [_el(text="Next"), _el(text="Filter")])
    assert a == b
    assert a != c, "an SPA state change with no URL change must be a new state"


# -- API surface: every explore route is behind dashboard JWT -----------------


def test_explore_routes_require_dashboard_auth():
    from fastapi.testclient import TestClient

    from navigator.app import main as app_module

    client = TestClient(app_module.app)
    for method, path in (
        ("GET", "/client/api/explore"),
        ("POST", "/client/api/explore/start"),
        ("POST", "/client/api/explore/stop"),
        ("POST", "/client/api/explore/answer"),
        ("POST", "/client/api/explore/ticket"),
    ):
        r = client.request(method, path, json={})
        assert r.status_code == 401, f"{method} {path} returned {r.status_code}"


def test_explore_websocket_rejects_a_bogus_ticket():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from navigator.app import main as app_module

    client = TestClient(app_module.app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/client/api/explore/ws?ticket=nope") as ws:
            ws.receive_json()


def test_explore_ticket_is_single_use_and_expires(monkeypatch):
    from navigator.automation.explore import tickets

    ticket = tickets.mint_ticket("acme")
    assert tickets.redeem_ticket(ticket) == "acme"
    assert tickets.redeem_ticket(ticket) is None, "a ticket must not be replayable"

    monkeypatch.setattr(tickets, "TTL_S", -1.0)
    assert tickets.redeem_ticket(tickets.mint_ticket("acme")) is None


# -- convergence: exploration output is recorder output ----------------------


def test_explored_steps_merge_through_the_recorder_path():
    """Same currency, same merge function, same unpublished draft."""
    from navigator.client.content import merge_recorded_flow
    from navigator.knowledge.site_graph import parse_site_graph

    page = FakePage("https://app.example.com/", [_el(testid="reports", text="Reports")])
    executed: list = []

    def _execute(_page, _graph, _page_id, call):
        executed.append(call)
        # Navigation to a new path — only first landing enters the demo flow.
        page.url = "https://app.example.com/reports"
        page.elements = [_el(testid="body", text="Reports page")]
        return ToolResult(ok=True, tool=call.tool, detail="ok", duration_ms=1), "main"

    session = _session(budget=ExplorationBudget(max_steps=2, max_pages=2))
    explore(
        session,
        _deps(
            page,
            executed=executed,
            execute=_execute,
            guard_judge=lambda _p: '{"destructive": false}',
        ),
    )
    assert session.steps
    assert session.actions_taken >= 1

    base = (
        "version: 1\nsite: acme\nbase_url: https://app.example.com\n"
        "pages:\n  main:\n    name: Main\n    url: /\n"
        "    selectors:\n      body: body\n    flows: {}\n"
    )
    merged = merge_recorded_flow(
        base,
        flow_name="Explored — Acme",
        flow_id=f"explored_{uuid4().hex[:8]}",
        page_id="main",
        steps=session.steps,
        product_name="Acme",
        base_url="https://app.example.com",
    )
    graph = parse_site_graph(merged)
    assert any(f.name.startswith("Explored") for f in graph.demo_playlist)


def test_merge_update_existing_replaces_flow_without_dup_playlist():
    from navigator.client.content import merge_recorded_flow
    from navigator.knowledge.site_graph import parse_site_graph
    from navigator.automation.record import RecordedStep

    base = (
        "version: 1\nsite: acme\nbase_url: https://app.example.com\n"
        "pages:\n  explore:\n    name: Explore\n    url: /\n"
        "    selectors:\n      body: body\n      inbox: '#inbox'\n"
        "    flows:\n      tour:\n        - tool: click_element\n"
        "          selector: inbox\n"
        "demo_playlist:\n  - order: 1\n    name: Old Tour\n"
        "    page_id: explore\n    flow_id: tour\n"
    )
    steps = [RecordedStep(tool="click_element", alias="inbox", selector="#inbox")]
    once = merge_recorded_flow(
        base,
        flow_name="Updated Tour",
        flow_id="tour",
        page_id="explore",
        steps=steps,
        product_name="Acme",
        base_url="https://app.example.com",
        update_existing=True,
    )
    graph = parse_site_graph(once)
    assert len([p for p in graph.demo_playlist if p.flow_id == "tour"]) == 1
    assert next(p for p in graph.demo_playlist if p.flow_id == "tour").name == "Updated Tour"


def test_backtrack_to_same_path_not_added_to_demo_flow(monkeypatch):
    """Explore may revisit a page; demo keeps only the first landing."""
    page = FakePage(
        "https://app.example.com/a",
        [_el(testid="go-b", text="Go B")],
    )
    executed: list = []
    hops = {"n": 0}

    def _execute(_page, _graph, _page_id, call):
        executed.append(call)
        hops["n"] += 1
        if hops["n"] == 1:
            page.url = "https://app.example.com/b"
            page.elements = [_el(testid="go-a", text="Go A")]
        else:
            page.url = "https://app.example.com/a"
            page.elements = [_el(testid="go-b", text="Go B")]
        return ToolResult(ok=True, tool=call.tool, detail="ok", duration_ms=1), "main"

    monkeypatch.setattr(
        "navigator.automation.explore.reason.choose_next",
        lambda **kw: __import__(
            "navigator.automation.explore.reason", fromlist=["Choice"]
        ).Choice(0, "nav", ""),
    )
    session = _session(budget=ExplorationBudget(max_steps=3, max_pages=4))
    explore(
        session,
        _deps(
            page,
            executed=executed,
            execute=_execute,
            guard_judge=lambda _p: '{"destructive": false}',
        ),
    )
    assert session.actions_taken >= 2
    # First hop /a→/b is a demo step; return /b→/a is explored only.
    assert len(session.steps) == 1
    assert session.steps[0].alias.replace("-", "_") == "go_b"


# -- disabled controls are never inventoried ---------------------------------


def test_inventory_skips_disabled_and_aria_disabled_elements():
    from navigator.automation.explore.perceive import inventory

    page = FakePage(
        "https://app.example.com/",
        [
            _el(testid="ok", text="Open"),
            _el(testid="nope", text="Save", disabled=True),
            _el(testid="aria", text="Publish", aria_disabled=True),
            _el(testid="css", text="Send", **{"class": "btn disabled"}),
        ],
    )
    # FakePage returns the list as-is from evaluate; inventory post-filters.
    got = inventory(page)
    assert [e["testid"] for e in got] == ["ok"]


def test_session_status_exposes_visited_paths_and_recent_events():
    session = _session()
    session.emit({"type": "log", "level": "info", "msg": "hello"})
    session.emit({"type": "explored", "path": "/inbox", "elements": 3})
    from navigator.automation.explore.session import StateFingerprint

    fp = StateFingerprint(url_path="/inbox", dom_hash="abc")
    session.visited[fp] = set()
    status = session.status()
    assert status["visited_paths"] == ["/inbox"]
    assert any(e.get("msg") == "hello" for e in status["recent_events"])
    assert "progress_pct" in status
    assert "elapsed_s" in status
    assert "budget" in status


def test_starting_phase_reports_active_true():
    """Start response must be active so the dashboard meter/poll can run."""
    session = _session(phase="starting")
    status = session.status()
    assert status["active"] is True
    assert status["phase"] == "starting"
    assert 0 <= status["progress_pct"] <= 100


def test_request_stop_marks_inactive_immediately():
    session = _session(phase="exploring")
    assert session.status()["active"] is True
    session.request_stop()
    status = session.status()
    assert status["active"] is False
    assert status["phase"] == "stopped"
    assert session.stop_event.is_set()


def test_progress_pct_tracks_unique_pages_not_time_floor():
    from navigator.automation.explore.session import StateFingerprint

    session = _session(budget=ExplorationBudget(max_pages=25, max_wall_clock_s=600))
    session.visited[StateFingerprint("/dashboard/", "a")] = set()
    session.visited[StateFingerprint("/inbox/", "b")] = set()
    # 2/25 → 8%, must not freeze at the old 20% soft time floor.
    assert session.status()["progress_pct"] == 8


def test_heuristic_skips_visited_nav_and_guardrail_fast_path():
    from navigator.automation.explore.guardrail import classify_action, looks_like_safe_nav
    from navigator.automation.explore.reason import heuristic_pick

    inbox = _el(testid="inbox", text="Inbox", tag="a", href="/inbox/")
    kanban = _el(testid="kanban", text="Kanban", tag="a", href="/kanban/")
    assert looks_like_safe_nav(inbox)
    assert classify_action(inbox, judge=None).flagged is False
    pick = heuristic_pick([inbox, kanban], visited_paths=["/dashboard/", "/inbox/"])
    assert pick is not None
    assert pick.index == 1  # kanban, not inbox


def test_visited_paths_dedupe_same_url_different_dom():
    from navigator.automation.explore.session import StateFingerprint

    session = _session()
    session.visited[StateFingerprint("/dashboard/", "aaa")] = set()
    session.visited[StateFingerprint("/kanban/", "bbb")] = set()
    session.visited[StateFingerprint("/dashboard/", "ccc")] = set()
    assert session.status()["visited_paths"] == ["/dashboard/", "/kanban/"]


def test_groq_retry_wait_parses_tpm_message():
    from navigator.automation.explore.runner import _groq_retry_wait_s

    assert _groq_retry_wait_s("try again in 1m0.48s", 0) == pytest.approx(61.48, abs=0.01)
    assert _groq_retry_wait_s("try again in 12.5s", 0) == pytest.approx(13.5, abs=0.01)
    assert _groq_retry_wait_s("rate_limit_exceeded", 0) == 15.0
