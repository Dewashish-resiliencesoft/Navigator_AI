"""Health-check CLI: credential gate + no password leakage."""

from __future__ import annotations

import os

import pytest

from navigator.automation.explore import health


def test_main_exits_cleanly_without_credential_key(monkeypatch, capsys):
    monkeypatch.setattr(
        "navigator.core.settings.settings.credential_key", ""
    )
    code = health.main(["--product-id", "acme"])
    assert code == 2
    err = capsys.readouterr().err
    assert "NAVIGATOR_CREDENTIAL_KEY" in err or "credential" in err.lower()
    assert "password" not in err.lower() or "password" in "credential"  # message may mention key


def test_dry_run_never_touches_vault(monkeypatch, tmp_path):
    from navigator.app.registry import Registry, NewProduct

    db = tmp_path / "reg.db"
    registry = Registry(db)
    registry.register(NewProduct(name="Acme", product_id="acme"))
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
      explored_x:
        - tool: wait_for
          selector: body
          expects: {check: visible, selector: body}
demo_playlist:
  - {order: 1, name: X, page_id: main, flow_id: explored_x}
_meta:
  semantics:
    explored_x:
      purpose: "Open the dashboard"
      tags: [dashboard]
"""
    registry.put_site_graph("acme", yaml_text, "explored", publish=False)

    monkeypatch.setattr("navigator.app.main.get_registry", lambda: registry)
    # Empty key would abort non-dry-run; dry-run must still work.
    monkeypatch.setattr("navigator.core.settings.settings.credential_key", "")
    code = health.main(["--product-id", "acme", "--dry-run"])
    assert code == 0
    latest = registry.latest_revision("acme")
    assert "validation" in latest.yaml
    assert "ready" in latest.yaml or "needs_review" in latest.yaml
