from pathlib import Path

import pytest

from navigator.meeting.live_demo import assert_live_site_graph


def test_assert_live_site_graph_rejects_fixture_path():
    with pytest.raises(RuntimeError, match="(?i)fixture|record"):
        assert_live_site_graph(Path("navigator/config/sites/whatsapp_crm.yaml"))


def test_assert_live_site_graph_rejects_temp_fixture_yaml(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "version: 1\nsite: test\nbase_url: ../../../tests/fixtures/\n"
        "pages:\n  inbox:\n    url: crm_dashboard.html\n"
    )
    with pytest.raises(RuntimeError, match="(?i)fixture|record"):
        assert_live_site_graph(path)


def test_assert_live_site_graph_accepts_live_yaml(tmp_path):
    path = tmp_path / "live.yaml"
    path.write_text(
        "version: 1\nsite: acme\nbase_url: https://app.acme.test/\n"
        "pages:\n  inbox:\n    url: inbox\n"
    )
    assert_live_site_graph(path)
