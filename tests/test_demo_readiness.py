"""Demo readiness scoring and live graph guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from navigator.agent.readiness import (
    _has_offerable_flow,
    assert_live_graph_yaml,
    assess_demo_readiness,
)
from navigator.app.registry import NewProduct, Registry


def test_assert_live_graph_yaml_rejects_fixture():
    with pytest.raises(ValueError, match="fixture"):
        assert_live_graph_yaml("pages:\n  - url: tests/fixtures/crm_dashboard.html\n")


def test_assess_demo_readiness_new_product(tmp_path: Path):
    db = tmp_path / "nav.db"
    with Registry(db) as reg:
        reg.register(NewProduct(product_id="acme", name="Acme", api_key="nav_test_key"))
        report = assess_demo_readiness(reg, "acme", origin="dashboard_test")
    assert report.score >= 0
    assert any(c.id == "published" for c in report.checks)
    ids = [c.id for c in report.checks]
    assert "tts" not in ids
    live = next(c for c in report.checks if c.id == "live")
    assert live.blocking is True


def test_has_offerable_flow_uses_page_keys(site_graph):
    """Regression: PageSpec has no .id — page_id comes from graph.pages keys."""
    assert _has_offerable_flow(site_graph) is True


def test_dashboard_readiness_uses_latest_draft_not_published(tmp_path: Path):
    """Regression: test demo runs latest revision; readiness must not inspect published only."""
    from navigator.knowledge.site_graph import parse_site_graph

    db = tmp_path / "nav.db"
    published_yaml = """
version: 1
site: acme
base_url: https://example.com/
persona:
  product_name: Acme
  one_liner: test
  agent_name: N
demo_playlist: []
pages:
  home:
    name: Home
    url: /
    selectors:
      body: body
    flows: {}
"""
    draft_yaml = """
version: 1
site: acme
base_url: https://example.com/
persona:
  product_name: Acme
  one_liner: test
  agent_name: N
demo_playlist:
  - order: 1
    name: Tour
    page_id: home
    flow_id: tour
pages:
  home:
    name: Home
    url: /
    selectors:
      body: body
    flows:
      tour:
        - tool: wait_for
          selector: body
          timeout_ms: 5000
          expects:
            check: visible
            selector: body
"""
    with Registry(db) as reg:
        reg.register(NewProduct(product_id="acme", name="Acme", api_key="nav_test_key"))
        reg.put_site_graph("acme", published_yaml, "yaml", publish=True)
        reg.put_site_graph("acme", draft_yaml, "yaml", publish=False)
        report = assess_demo_readiness(reg, "acme", origin="dashboard_test")
    by_id = {c.id: c for c in report.checks}
    assert by_id["offerable_flow"].ok is True
    assert by_id["playlist"].ok is True
    pub_report = assess_demo_readiness(reg, "acme", origin="public_embed")
    pub_by_id = {c.id: c for c in pub_report.checks}
    assert pub_by_id["offerable_flow"].ok is False
    assert pub_by_id["playlist"].ok is False
    assert "publish draft revision" in pub_by_id["offerable_flow"].message
    assert "publish draft revision" in pub_by_id["playlist"].message


def test_explorer_blocks_public_embed(tmp_path: Path):
    db = tmp_path / "nav.db"
    with Registry(db) as reg:
        reg.register(NewProduct(product_id="acme", name="Acme", api_key="nav_test_key"))
        reg.set_autonomy_mode("acme", "explorer")
        report = assess_demo_readiness(reg, "acme", origin="public_embed")
    blocked = [c for c in report.checks if c.id == "explorer_embed"]
    assert blocked and not blocked[0].ok and blocked[0].blocking


def test_readiness_does_not_import_live_demo():
    """Dashboard polls readiness; importing live_demo pulls Playwright into uvicorn RSS."""
    import inspect

    from navigator.agent import readiness

    src = inspect.getsource(readiness._attendee_ok)
    assert "live_demo" not in src
    assert "attendee_stack" in src
