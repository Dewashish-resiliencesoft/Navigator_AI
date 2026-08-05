from pathlib import Path

import pytest

from navigator.meeting import live_demo
from navigator.meeting.live_demo import (
    _require_live_settings,
    assert_live_site_graph,
    share_media_join_opts,
)
from navigator.core.settings import settings


def test_assert_live_site_graph_rejects_fixture_path():
    with pytest.raises(RuntimeError, match="(?i)fixture|record"):
        assert_live_site_graph(Path("navigator/knowledge/sites/whatsapp_crm.yaml"))


def test_share_media_join_opts_parity():
    meet_reserve, meet_sdk = share_media_join_opts(is_zoom=False)
    zoom_reserve, zoom_sdk = share_media_join_opts(is_zoom=True)
    assert meet_reserve is True and meet_sdk is None
    # Zoom: no voice-agent reserve, no web SDK (host join must succeed).
    assert zoom_reserve is False and zoom_sdk is None


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


def test_self_hosted_localhost_attendee_is_accepted(monkeypatch):
    """Self-hosting is the free path, so a *live* localhost instance is valid."""
    monkeypatch.setattr(settings, "attendee_base_url", "http://localhost:8002/api/v1")
    monkeypatch.setattr(live_demo, "_attendee_reachable", lambda url: True)
    _require_live_settings("https://meet.google.com/x")


def test_unreachable_attendee_is_refused(monkeypatch):
    """An unconfigured default looks like localhost too — refuse when it's dead."""
    monkeypatch.setattr(settings, "attendee_base_url", "http://localhost:8002/api/v1")
    monkeypatch.setattr(live_demo, "_attendee_reachable", lambda url: False)
    with pytest.raises(RuntimeError, match="unreachable"):
        _require_live_settings("https://meet.google.com/x")


def test_reachability_probe_treats_an_http_error_as_alive(monkeypatch):
    """Attendee answers /bots with 401 when unauthenticated — that means it's up."""
    from urllib.error import HTTPError, URLError

    def raise_401(url, timeout=None):
        raise HTTPError(url, 401, "Unauthorized", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr("navigator.meeting.attendee_stack.urlopen", raise_401)
    assert live_demo._attendee_reachable("http://localhost:8002/api/v1") is True

    def raise_refused(url, timeout=None):
        raise URLError("Connection refused")

    monkeypatch.setattr("navigator.meeting.attendee_stack.urlopen", raise_refused)
    assert live_demo._attendee_reachable("http://localhost:8002/api/v1") is False


def test_missing_attendee_key_is_still_refused(monkeypatch):
    monkeypatch.setattr(settings, "attendee_api_key", "")
    with pytest.raises(RuntimeError, match="ATTENDEE_API_KEY"):
        _require_live_settings("https://meet.google.com/x")
