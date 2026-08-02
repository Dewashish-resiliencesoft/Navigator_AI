"""Session-expiry recovery vs permissions-denied; resume re-nav after detour."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from navigator.agent.nodes.planning import _ensure_browser_on_page
from navigator.agent.nodes.verifying import SESSION_STALL_LINE, verifying
from navigator.agent.state import CallDeps, initial_state
from navigator.automation.login_match import LoginConfig
from navigator.core.schemas import (
    ClickElement,
    Postcondition,
    ToolResult,
    VerifyResult,
)
from navigator.voice.tts import PrintSpeaker


def _deps(site_graph, page, log, tmp_path, **kwargs):
    return CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        product_id="acme",
        archive_dir=tmp_path / "archives",
        **kwargs,
    )


def test_session_expiry_retries_without_failure(
    site_graph, log, tmp_path, state, monkeypatch
):
    call = ClickElement(
        selector="compose",
        expects=Postcondition(check="visible", selector="compose", timeout_ms=1000),
    )
    state["last_call"] = call
    state["last_result"] = ToolResult(
        ok=True, tool="click_element", detail="ok", duration_ms=1
    )
    state["last_page_id"] = "inbox"
    state["pending_calls"] = []

    page = MagicMock()
    page.url = "https://example.com/login"
    page.inner_text = MagicMock(return_value="Sign in")

    relogin_calls = []

    monkeypatch.setattr(
        "navigator.agent.nodes.verifying.check",
        lambda *a, **k: VerifyResult(passed=False, actual="missing"),
    )

    deps = _deps(
        site_graph,
        page,
        log,
        tmp_path,
        login_config=LoginConfig(login_url="https://example.com/login"),
        relogin=lambda: relogin_calls.append(1) or True,
    )
    out = verifying(state, deps)
    assert out["pending_calls"][0] is call
    assert SESSION_STALL_LINE in out["narration"][0]
    assert out.get("failures") in (None, [])
    assert relogin_calls == [1]


def test_permission_denied_does_not_relogin(
    site_graph, log, tmp_path, state, monkeypatch
):
    call = ClickElement(
        selector="compose",
        expects=Postcondition(check="visible", selector="compose", timeout_ms=1000),
    )
    state["last_call"] = call
    state["last_result"] = ToolResult(
        ok=True, tool="click_element", detail="ok", duration_ms=1
    )
    state["last_page_id"] = "inbox"

    page = MagicMock()
    page.url = "https://example.com/403"
    page.inner_text = MagicMock(return_value="Access denied")

    monkeypatch.setattr(
        "navigator.agent.nodes.verifying.check",
        lambda *a, **k: VerifyResult(passed=False, actual="missing"),
    )
    relogin_calls = []
    deps = _deps(
        site_graph,
        page,
        log,
        tmp_path,
        login_config=LoginConfig(login_url="https://example.com/login"),
        relogin=lambda: relogin_calls.append(1) or True,
    )
    out = verifying(state, deps)
    assert relogin_calls == []
    assert out.get("failures")


def test_ensure_browser_renav_when_diverged(site_graph, tmp_path, log):
    page = MagicMock()
    page.url = "https://example.com/settings/elsewhere"
    page.goto = MagicMock()
    deps = _deps(site_graph, page, log, tmp_path)
    _ensure_browser_on_page(deps, "inbox")
    assert page.goto.called
    target = page.goto.call_args[0][0]
    assert "inbox" in target or target.endswith(
        site_graph.page("inbox").url.lstrip("/")
    ) or site_graph.url_for("inbox") in target


def test_ensure_browser_skips_when_same_path(site_graph, tmp_path, log):
    page = MagicMock()
    expected = site_graph.url_for("inbox")
    page.url = expected
    page.goto = MagicMock()
    deps = _deps(site_graph, page, log, tmp_path)
    _ensure_browser_on_page(deps, "inbox")
    assert not page.goto.called
