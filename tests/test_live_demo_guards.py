from pathlib import Path

import pytest

from navigator.meeting.live_demo import _require_live_settings, assert_live_site_graph
from navigator.core.settings import settings


def test_assert_live_site_graph_rejects_fixture_path():
    with pytest.raises(RuntimeError, match="(?i)fixture|record"):
        assert_live_site_graph(Path("navigator/knowledge/sites/whatsapp_crm.yaml"))


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


# -- meeting url: explicit arg wins, env is the CLI fallback ------------------


@pytest.fixture(autouse=True)
def _attendee_configured(monkeypatch):
    monkeypatch.setattr(settings, "attendee_base_url", "https://app.attendee.dev/api/v1")
    monkeypatch.setattr(settings, "attendee_api_key", "tok")


def test_cli_falls_back_to_the_env_var(monkeypatch):
    """`python -m navigator.meeting.live_demo` with no URL still works."""
    monkeypatch.setattr(settings, "meeting_url", "https://meet.google.com/env-link")
    _require_live_settings(settings.meeting_url)  # what the CLI path resolves to


def test_an_explicit_url_needs_no_env_var(monkeypatch):
    monkeypatch.setattr(settings, "meeting_url", "")
    _require_live_settings("https://meet.google.com/created-per-session")


def test_no_url_from_either_source_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "meeting_url", "")
    with pytest.raises(RuntimeError, match="meeting_url"):
        _require_live_settings("")


def test_localhost_attendee_is_still_refused(monkeypatch):
    monkeypatch.setattr(settings, "attendee_base_url", "http://localhost:8000/api/v1")
    with pytest.raises(RuntimeError, match="localhost"):
        _require_live_settings("https://meet.google.com/x")


def test_missing_attendee_key_is_still_refused(monkeypatch):
    monkeypatch.setattr(settings, "attendee_api_key", "")
    with pytest.raises(RuntimeError, match="ATTENDEE_API_KEY"):
        _require_live_settings("https://meet.google.com/x")
