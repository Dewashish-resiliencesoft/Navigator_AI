"""System health endpoint for Client dashboard."""

from __future__ import annotations

from unittest.mock import MagicMock

from navigator.app.system_health import collect_system_health


def test_collect_system_health_shape():
    registry = MagicMock()
    registry.latest_revision.return_value = MagicMock(revision=1)
    runner = MagicMock()
    runner.list.return_value = []

    payload = collect_system_health(
        product_id="acme",
        registry=registry,
        runner=runner,
        db_path=":memory:",
    )

    assert "cpu_percent" in payload
    assert "gpu" in payload
    assert payload["gpu"]["active"] in (True, False)
    assert len(payload["services"]) >= 4
    assert len(payload["health"]) >= 2
