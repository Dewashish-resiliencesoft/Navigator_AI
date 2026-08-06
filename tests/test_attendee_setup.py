"""Attendee .env sync helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from navigator.core.settings import settings
from navigator.meeting.attendee_setup import (
    AttendeeSetupError,
    _attendee_zoom_sdk_credentials,
    sync_attendee_zoom_credentials,
)


def test_attendee_zoom_sdk_credentials_reads_dedicated_vars(monkeypatch):
    monkeypatch.setattr(settings, "attendee_zoom_client_id", "sdk-id")
    monkeypatch.setattr(settings, "attendee_zoom_client_secret", "sdk-secret")
    assert _attendee_zoom_sdk_credentials() == ("sdk-id", "sdk-secret")


def test_sync_missing_credentials_raises(monkeypatch):
    monkeypatch.setattr(settings, "attendee_zoom_client_id", "")
    monkeypatch.setattr(settings, "attendee_zoom_client_secret", "")
    try:
        sync_attendee_zoom_credentials()
    except AttendeeSetupError as exc:
        assert "NAVIGATOR_ATTENDEE_ZOOM_CLIENT_ID" in str(exc)
    else:
        raise AssertionError("expected AttendeeSetupError")


def test_sync_attendee_zoom_credentials_runs_docker_exec(monkeypatch, tmp_path):
    compose = tmp_path / "attendee"
    compose.mkdir()
    (compose / "dev.docker-compose.yaml").write_text("services: {}\n")

    monkeypatch.setattr(settings, "attendee_zoom_client_id", "sdk-id")
    monkeypatch.setattr(settings, "attendee_zoom_client_secret", "sdk-secret")
    monkeypatch.setattr(settings, "attendee_compose_dir", compose)

    proc = MagicMock(returncode=0, stdout="attendee_zoom_sync_ok proj_x\n", stderr="")
    with patch("navigator.meeting.attendee_setup.subprocess.run", return_value=proc) as run:
        sync_attendee_zoom_credentials()

    assert run.call_args.kwargs["cwd"] == compose
    cmd = run.call_args.args[0]
    assert "attendee-app-local" in cmd
    assert "sdk-id" in cmd[-1]
