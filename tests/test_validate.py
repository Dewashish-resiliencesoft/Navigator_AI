"""Flow validation: risk score, verdict, live-agent offerability."""

from __future__ import annotations

from navigator.automation.explore import validate
from navigator.core.schemas import ClickElement, Postcondition, ToolResult, VerifyResult


def test_delete_flow_never_ready_even_at_full_pass():
    result = validate.verdict_for(
        purpose="Delete a customer account",
        tags=["delete", "account"],
        step_descriptions=["Deletes the account"],
        n_steps=3,
        pass_rate=1.0,
    )
    assert result.verdict != "ready"
    # +50 delete − 30 pass_rate → 20 raw, but the financial/destructive gate
    # still forces needs_review regardless of the numeric floor.
    assert result.reason.startswith("destructive")


def test_financial_flow_never_ready():
    result = validate.verdict_for(
        purpose="Charge the customer's card",
        tags=["pay", "checkout"],
        n_steps=4,
        pass_rate=1.0,
    )
    assert result.verdict == "needs_review"
    assert result.risk_score >= 70  # +100 pay − 30 pass
    assert "financial" in result.reason


def test_safe_high_pass_is_ready():
    result = validate.verdict_for(
        purpose="Open the analytics dashboard",
        tags=["analytics", "view"],
        step_descriptions=["Opens analytics", "Shows charts"],
        n_steps=3,
        pass_rate=1.0,
    )
    assert result.verdict == "ready"
    assert result.risk_score < 30


def test_low_pass_rate_is_broken():
    result = validate.verdict_for(
        purpose="Open settings",
        n_steps=4,
        pass_rate=0.25,
        failed_step_idxs=(1, 2, 3),
    )
    assert result.verdict == "broken"


def test_send_adds_risk():
    score = validate.risk_score(
        purpose="Send an email",
        tags=["send"],
        n_steps=3,
        pass_rate=1.0,
    )
    assert score >= 10  # +40 send -30 pass


def test_single_step_adds_risk():
    score = validate.risk_score(purpose="Opens page", n_steps=1, pass_rate=1.0)
    assert score >= 0  # +20 -30 = 0 floor possible; just ensure no crash
    score_raw = 20 - 30  # before floor
    assert validate.risk_score(purpose="x", n_steps=1, pass_rate=0.0) >= 20


def test_validate_flow_replays_and_counts_failures():
    calls = [
        ClickElement(selector="a", expects=Postcondition(check="visible", selector="a")),
        ClickElement(selector="b", expects=Postcondition(check="visible", selector="b")),
    ]
    results = [
        (ToolResult(ok=True, tool="click_element", detail="ok", duration_ms=1), "main"),
        (ToolResult(ok=False, tool="click_element", detail="missing", duration_ms=1), "main"),
    ]
    idx = {"i": 0}

    def execute(_p, _g, _pid, _call):
        r = results[idx["i"]]
        idx["i"] += 1
        return r

    def verify(_p, _g, _pid, _exp):
        return VerifyResult(passed=True, actual="ok")

    out = validate.validate_flow(
        steps=calls,
        page=object(),
        graph=object(),
        page_id="main",
        execute=execute,
        verify=verify,
        purpose="Open billing",
        tags=["billing"],
    )
    assert out.failed_step_idxs == (1,)
    assert out.pass_rate == 0.5
    assert out.verdict in {"broken", "needs_review"}


def test_is_offerable_rules():
    assert validate.is_offerable(None) is True
    assert validate.is_offerable({}) is True
    assert validate.is_offerable({"verdict": "ready"}) is True
    assert validate.is_offerable({"verdict": "broken"}) is False
    assert validate.is_offerable({"verdict": "needs_review"}) is False


def test_planning_excludes_broken_flows():
    from navigator.agent.nodes.planning import _flow_texts_for_page
    from navigator.knowledge.site_graph import parse_site_graph

    yaml_text = """
version: 1
site: acme
base_url: https://app.example.com
pages:
  main:
    name: Main
    url: /
    selectors: {body: body}
    flows:
      explored_ok:
        - tool: wait_for
          selector: body
          expects: {check: visible, selector: body}
      explored_bad:
        - tool: wait_for
          selector: body
          expects: {check: visible, selector: body}
demo_playlist:
  - {order: 1, name: Ok, page_id: main, flow_id: explored_ok}
  - {order: 2, name: Bad, page_id: main, flow_id: explored_bad}
_meta:
  validation:
    explored_ok: {verdict: ready, pass_rate: 1.0, risk_score: 0, failed_step_idxs: []}
    explored_bad: {verdict: broken, pass_rate: 0.0, risk_score: 99, failed_step_idxs: [0]}
"""

    class _Deps:
        def __init__(self, graph):
            self.graph = graph

    texts = _flow_texts_for_page(_Deps(parse_site_graph(yaml_text)), "main")
    assert "explored_ok" in texts
    assert "explored_bad" not in texts
