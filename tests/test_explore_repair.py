"""Self-healing explore: diagnose, repair ladder, episodes, learn."""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

import pytest

from navigator.automation.explore.diagnose import StuckKind, classify, looks_nav_stalled
from navigator.automation.explore.episode import EpisodeStore, StepAttempt, StopReason
from navigator.automation.explore.explorer import ExplorerDeps, explore
from navigator.automation.explore.learn import draft_rules
from navigator.automation.explore.repair import alternate_selectors, tactics_for
from navigator.automation.explore.session import (
    ExplorationBudget,
    ExplorationSession,
    StateFingerprint,
    fingerprint,
)
from navigator.core.schemas import ToolResult, VerifyResult
from navigator.knowledge.memory.pending import PendingCorrectionStore


class FakePage:
    def __init__(self, url: str, elements: list[dict]) -> None:
        self.url = url
        self.elements = elements

    def evaluate(self, _js: str) -> list[dict]:
        return self.elements

    def screenshot(self, **_kw) -> bytes:
        return b"\xff\xd8\xfffakejpeg"

    def wait_for_timeout(self, _ms: int) -> None:
        return None

    def go_back(self, timeout: int = 8000) -> None:
        raise RuntimeError("no history")


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


# -- 1. StuckKind classification ---------------------------------------------


@pytest.mark.parametrize(
    "detail,expected",
    [
        ("Timeout 5000ms exceeded.\nwaiting for locator('[data-testid=\"x\"]')", "not_found"),
        ("element is not visible", "not_visible"),
        ("<div> intercepts pointer events", "intercepted"),
        ("element is not enabled — disabled", "disabled"),
        ("Element is not attached to the DOM", "detached"),
        ("Timeout 15000ms exceeded", "timeout"),
    ],
)
def test_stuck_kind_from_playwright_detail(detail: str, expected: StuckKind):
    result = ToolResult(ok=False, tool="click_element", detail=detail, duration_ms=1)
    assert classify(result) == expected


def test_nav_stalled_when_ok_but_state_unchanged():
    fp = StateFingerprint("/", "abc")
    assert looks_nav_stalled(
        fillable=False,
        result_ok=True,
        url_before="https://app.example.com/",
        url_after="https://app.example.com/",
        fp_before=fp,
        fp_after=fp,
    )
    assert classify(
        ToolResult(ok=True, tool="click_element", detail="clicked", duration_ms=1),
        nav_stalled=True,
    ) == "nav_stalled"


def test_verify_failed_kind():
    result = ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1)
    assert classify(result, verify_passed=False, verify_actual="missing") == "verify_failed"


# -- 2. Repair feeds demo flow -----------------------------------------------


def test_not_found_repairs_via_alternate_selector_lands_in_steps(monkeypatch, tmp_path):
    el = _el(testid="billing-nav", id="billing", text="Billing", tag="a", href="/billing")
    page = FakePage("https://app.example.com/", [el])
    episode = EpisodeStore(root=tmp_path / "ep", product_id="acme", job_id="j1")
    executed: list = []

    def _execute(_page, graph, _page_id, call):
        executed.append(call)
        css = graph.selector("main", call.selector)
        if css.startswith("#") or "has-text" in css or css.startswith("text="):
            page.url = "https://app.example.com/billing"
            page.elements = [_el(testid="invoice", text="Invoices")]
            return ToolResult(ok=True, tool=call.tool, detail="ok", duration_ms=1), "main"
        return (
            ToolResult(
                ok=False,
                tool=call.tool,
                detail='Timeout 5000ms exceeded.\nwaiting for locator(\'[data-testid="billing-nav"]\')',
                duration_ms=1,
            ),
            "main",
        )

    def _verify(_page, _graph, _page_id, _expects):
        return VerifyResult(passed=True, actual="ok")

    monkeypatch.setattr(
        "navigator.automation.explore.reason.choose_next",
        lambda **kw: __import__(
            "navigator.automation.explore.reason", fromlist=["Choice"]
        ).Choice(0, "go billing", ""),
    )

    session = _session(
        budget=ExplorationBudget(max_steps=3, max_pages=5, max_repairs_per_step=3),
    )
    explore(
        session,
        ExplorerDeps(
            page=page,
            execute=_execute,
            verify=_verify,
            episode=episode,
            guard_judge=lambda _p: '{"destructive": false}',
        ),
    )

    assert session.steps, "repaired click must enter the demo flow"
    assert any(a.attempt > 0 and a.ok for a in episode.attempts)
    assert episode.attempts[0].attempt == 0
    assert episode.attempts[0].ok is False


# -- 3. Guardrail blocks overlay dismiss -------------------------------------


def test_guardrail_flagged_overlay_not_dismissed(monkeypatch, tmp_path):
    target = _el(testid="settings", text="Settings")
    # Destructive-looking dismiss — keyword guardrail must block it.
    overlay = _el(testid="wipe-all", text="Delete everything and accept")
    page = FakePage("https://app.example.com/", [target, overlay])
    episode = EpisodeStore(root=tmp_path / "ep", product_id="acme", job_id="j2")
    dismissed: list[str] = []

    def _execute(_page, graph, _page_id, call):
        css = graph.selector("main", call.selector)
        dismissed.append(css)
        if "wipe" in css or "delete" in css.lower():
            return ToolResult(ok=True, tool=call.tool, detail="dismissed", duration_ms=1), "main"
        return (
            ToolResult(
                ok=False,
                tool=call.tool,
                detail="<div class=banner> intercepts pointer events",
                duration_ms=1,
            ),
            "main",
        )

    monkeypatch.setattr(
        "navigator.automation.explore.reason.choose_next",
        lambda **kw: __import__(
            "navigator.automation.explore.reason", fromlist=["Choice"]
        ).Choice(0, "settings", ""),
    )

    session = _session(
        budget=ExplorationBudget(max_steps=2, max_pages=2, max_repairs_per_step=2),
    )
    explore(
        session,
        ExplorerDeps(
            page=page,
            execute=_execute,
            verify=lambda *_a, **_k: VerifyResult(passed=True, actual="ok"),
            episode=episode,
            # No judge → keyword layer flags Delete.
            guard_judge=None,
        ),
    )

    assert not any("wipe" in c or "delete" in c.lower() for c in dismissed), (
        "repair must not click a guardrail-flagged overlay control"
    )


# -- 4. max_repairs_per_step -------------------------------------------------


def test_max_repairs_per_step_honoured(monkeypatch, tmp_path):
    el = _el(testid="ghost", id="ghost", text="Ghost", name="ghost")
    page = FakePage("https://app.example.com/", [el])
    episode = EpisodeStore(root=tmp_path / "ep", product_id="acme", job_id="j3")
    calls = {"n": 0}

    def _execute(_page, _graph, _page_id, call):
        calls["n"] += 1
        return (
            ToolResult(
                ok=False,
                tool=call.tool,
                detail="waiting for locator — TimeoutError",
                duration_ms=1,
            ),
            "main",
        )

    monkeypatch.setattr(
        "navigator.automation.explore.reason.choose_next",
        lambda **kw: __import__(
            "navigator.automation.explore.reason", fromlist=["Choice"]
        ).Choice(0, "ghost", ""),
    )

    session = _session(
        budget=ExplorationBudget(
            max_steps=1, max_pages=1, max_repairs_per_step=2, max_repairs_total=10
        ),
    )
    explore(
        session,
        ExplorerDeps(
            page=page,
            execute=_execute,
            verify=lambda *_a, **_k: VerifyResult(passed=False, actual="no"),
            episode=episode,
            guard_judge=lambda _p: '{"destructive": false}',
        ),
    )

    # 1 original + at most 2 repairs
    assert calls["n"] <= 3
    assert session.repairs_used <= 2
    repair_rows = [a for a in episode.attempts if a.attempt > 0]
    assert len(repair_rows) <= 2


# -- 5. nav_stalled detection in loop ----------------------------------------


def test_nav_stalled_detected_for_noop_nav_click(monkeypatch, tmp_path):
    el = _el(tag="a", href="/billing", text="Billing", testid="nav-billing")
    page = FakePage("https://app.example.com/", [el])
    episode = EpisodeStore(root=tmp_path / "ep", product_id="acme", job_id="j4")

    def _execute(_page, _graph, _page_id, call):
        # Click "succeeds" but URL + DOM stay put.
        return ToolResult(ok=True, tool=call.tool, detail="clicked", duration_ms=1), "main"

    monkeypatch.setattr(
        "navigator.automation.explore.reason.choose_next",
        lambda **kw: __import__(
            "navigator.automation.explore.reason", fromlist=["Choice"]
        ).Choice(0, "billing", ""),
    )

    session = _session(
        budget=ExplorationBudget(max_steps=1, max_pages=2, max_repairs_per_step=1),
    )
    explore(
        session,
        ExplorerDeps(
            page=page,
            execute=_execute,
            verify=lambda *_a, **_k: VerifyResult(passed=True, actual="ok"),
            episode=episode,
            guard_judge=lambda _p: '{"destructive": false}',
        ),
    )

    assert episode.attempts
    assert episode.attempts[0].kind == "nav_stalled"
    assert episode.attempts[0].ok is False


# -- 6. attempts.jsonl -------------------------------------------------------


def test_attempts_jsonl_has_original_and_repairs(monkeypatch, tmp_path):
    el = _el(testid="x", id="x", text="X")
    page = FakePage("https://app.example.com/", [el])
    episode = EpisodeStore(root=tmp_path / "ep", product_id="acme", job_id="j5")

    def _execute(_page, _graph, _page_id, call):
        return (
            ToolResult(ok=False, tool=call.tool, detail="waiting for locator", duration_ms=1),
            "main",
        )

    monkeypatch.setattr(
        "navigator.automation.explore.reason.choose_next",
        lambda **kw: __import__(
            "navigator.automation.explore.reason", fromlist=["Choice"]
        ).Choice(0, "x", ""),
    )

    session = _session(
        budget=ExplorationBudget(max_steps=1, max_pages=1, max_repairs_per_step=2),
    )
    explore(
        session,
        ExplorerDeps(
            page=page,
            execute=_execute,
            verify=lambda *_a, **_k: VerifyResult(passed=False, actual="no"),
            episode=episode,
            guard_judge=lambda _p: '{"destructive": false}',
        ),
    )

    lines = episode.attempts_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "attempts.jsonl must be written live"
    rows = [json.loads(line) for line in lines]
    assert rows[0]["attempt"] == 0
    assert len(rows) >= 1


# -- 7. Screenshot cap -------------------------------------------------------


def test_screenshot_cap_at_20(tmp_path):
    store = EpisodeStore(root=tmp_path / "ep", product_id="acme", job_id="j6")
    for _ in range(30):
        store.save_shot(b"\xff\xd8\xffshot")
    assert store._shots_written == 20
    assert len(list(store.shots_dir.glob("*.jpg"))) == 20


# -- 8. learn drafts pending, never Chroma -----------------------------------


def test_learn_drafts_pending_not_chroma(tmp_path):
    pytest.importorskip("chromadb")
    from navigator.knowledge.memory.collections import get_collection

    chroma = tmp_path / "chroma"
    coll = get_collection(chroma, "acme", "corrections")
    before = coll.count()

    store = EpisodeStore(root=tmp_path / "ep", product_id="acme", job_id="j7")
    store.record(
        StepAttempt(
            element_key="testid=x",
            alias="x",
            selector='[data-testid="x"]',
            tool="click_element",
            attempt=0,
            tactic="",
            kind="intercepted",
            ok=False,
            detail="banner intercepts",
            duration_ms=1,
            url_before="https://app.example.com/billing",
            url_after="https://app.example.com/billing",
        )
    )
    store.record(
        StepAttempt(
            element_key="testid=x",
            alias="x",
            selector='[data-testid="x"]',
            tool="click_element",
            attempt=1,
            tactic="dismiss_overlay",
            kind="",
            ok=True,
            detail="ok",
            duration_ms=1,
            url_before="https://app.example.com/billing",
            url_after="https://app.example.com/billing",
        )
    )

    pending_db = tmp_path / "pending.db"
    rules = draft_rules(
        store,
        product_id="acme",
        session_id=str(uuid4()),
        pending_db_path=pending_db,
        complete=lambda _sys, _user: "On /billing dismiss the cookie banner before clicking nav.",
    )
    assert rules
    with PendingCorrectionStore(pending_db) as pending:
        listed = pending.list_pending("acme")
    assert listed
    assert listed[0].page == "billing"
    assert coll.count() == before, "learn must never write Chroma"


# -- 9. Retention purge ------------------------------------------------------


def test_old_episode_dirs_purged_on_open(tmp_path):
    root = tmp_path / "ep"
    old = root / "acme" / "oldjob"
    old.mkdir(parents=True)
    (old / "episode.json").write_text("{}")
    # Age the directory past retention.
    old_mtime = time.time() - 8 * 86400
    import os

    os.utime(old, (old_mtime, old_mtime))

    EpisodeStore(root=root, product_id="acme", job_id="newjob", retention_days=7)
    assert not old.exists()
    assert (root / "acme" / "newjob").is_dir()


def test_stop_reason_render_stable():
    assert StopReason("max_pages", "25").render() == "max_pages (25) reached"
    assert StopReason.from_budget_text("dead end at /billing").kind == "dead_end"


def test_alternate_selectors_ranked():
    el = _el(testid="t", id="i", name="n", text="Hello", role="button")
    alts = alternate_selectors(el)
    assert alts[0][1] == '[data-testid="t"]'
    assert any(css.startswith("#") for _, css in alts)


def test_tactics_for_kinds():
    assert "alternate_selector" in tactics_for("not_found")
    assert "dismiss_overlay" in tactics_for("intercepted")
    assert tactics_for("disabled") == ()


def test_live_graph_add_overwrites_css():
    from navigator.automation.explore.explorer import _LiveGraph

    graph = _LiveGraph("https://app.example.com")
    graph.add("btn", "#old")
    graph.add("btn", "#new")
    assert graph.selector("main", "btn") == "#new"
