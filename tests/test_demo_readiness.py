"""Demo readiness scoring and live graph guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from navigator.agent.readiness import assert_live_graph_yaml, assess_demo_readiness
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


def test_explorer_blocks_public_embed(tmp_path: Path):
    db = tmp_path / "nav.db"
    with Registry(db) as reg:
        reg.register(NewProduct(product_id="acme", name="Acme", api_key="nav_test_key"))
        reg.set_autonomy_mode("acme", "explorer")
        report = assess_demo_readiness(reg, "acme", origin="public_embed")
    blocked = [c for c in report.checks if c.id == "explorer_embed"]
    assert blocked and not blocked[0].ok and blocked[0].blocking
