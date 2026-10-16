from pathlib import Path
from unittest.mock import MagicMock

import pytest

from navigator.core.settings import settings
from navigator.meeting import attendee_stack


def test_is_local_attendee_url():
    assert attendee_stack.is_local_attendee_url("http://localhost:8002/api/v1")
    assert attendee_stack.is_local_attendee_url("http://127.0.0.1:8002/api/v1")
    assert not attendee_stack.is_local_attendee_url("https://app.attendee.dev/api/v1")


def test_ensure_skips_cloud_url(monkeypatch):
    monkeypatch.setattr(attendee_stack, "_docker_compose_up", MagicMock())
    monkeypatch.setattr(attendee_stack, "attendee_reachable", lambda _url: False)
    assert attendee_stack.ensure_attendee_stack(
        base_url="https://app.attendee.dev/api/v1",
        autostart=True,
    ) is False


def test_ensure_skips_when_already_up(monkeypatch, tmp_path):
    monkeypatch.setattr(attendee_stack, "_compose_dir", lambda: tmp_path)
    monkeypatch.setattr(attendee_stack, "attendee_reachable", lambda _url: True)
    (tmp_path / ".navigator-compose-id").write_text(attendee_stack._COMPOSE_ID)
    docker = MagicMock()
    monkeypatch.setattr(attendee_stack, "_docker_compose_up", docker)

    assert attendee_stack.ensure_attendee_stack(autostart=True) is True
    docker.assert_not_called()


def test_ensure_starts_compose_when_down(monkeypatch, tmp_path):
    for name in attendee_stack._COMPOSE_FILES:
        (tmp_path / name).write_text("services: {}\n")

    states = iter([False, False, True])

    def reachable(_url):
        return next(states)

    monkeypatch.setattr(attendee_stack, "_compose_dir", lambda: tmp_path)
    monkeypatch.setattr(attendee_stack, "attendee_reachable", reachable)
    monkeypatch.setattr(attendee_stack, "_in_pytest", lambda: False)

    proc = MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(
        attendee_stack,
        "_docker_compose_up",
        lambda _d, force_recreate=False: proc,
    )
    monkeypatch.setattr(attendee_stack.time, "sleep", lambda _s: None)

    assert attendee_stack.ensure_attendee_stack(autostart=True, wait_timeout_s=10) is True


def test_ensure_skips_in_pytest(monkeypatch):
    monkeypatch.setattr(attendee_stack, "_in_pytest", lambda: True)
    docker = MagicMock()
    monkeypatch.setattr(attendee_stack, "_docker_compose_up", docker)
    monkeypatch.setattr(attendee_stack, "attendee_reachable", lambda _url: False)

    assert attendee_stack.ensure_attendee_stack(autostart=True) is False
    docker.assert_not_called()


def _force_stock_compose_dir(monkeypatch):
    monkeypatch.delenv("NAVIGATOR_ATTENDEE_COMPOSE_DIR", raising=False)
    monkeypatch.setattr(settings, "attendee_compose_dir", attendee_stack._stock_attendee_dir())


def test_compose_dir_follows_attendee_compose_dir(monkeypatch, tmp_path):
    _force_stock_compose_dir(monkeypatch)
    monkeypatch.setenv("ATTENDEE_COMPOSE_DIR", str(tmp_path))
    assert attendee_stack._compose_dir() == tmp_path


def test_navigator_compose_dir_wins_over_attendee_compose_dir(monkeypatch, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("NAVIGATOR_ATTENDEE_COMPOSE_DIR", str(tmp_path))
    monkeypatch.setenv("ATTENDEE_COMPOSE_DIR", str(other))
    assert attendee_stack._compose_dir() == tmp_path


def test_webpage_streamer_cwd_follows_attendee_compose_dir(monkeypatch, tmp_path):
    _force_stock_compose_dir(monkeypatch)
    monkeypatch.setenv("ATTENDEE_COMPOSE_DIR", str(tmp_path))
    monkeypatch.setattr(attendee_stack, "_in_pytest", lambda: False)
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        seen["cmd"] = cmd
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(attendee_stack.subprocess, "run", fake_run)
    assert attendee_stack.ensure_webpage_streamer() is True
    assert seen["cwd"] == tmp_path
    assert "attendee-webpage-streamer-local" in seen["cmd"]


def test_compose_up_requires_files(tmp_path):
    with pytest.raises(FileNotFoundError, match="missing"):
        attendee_stack._docker_compose_up(tmp_path)


def test_meeting_sdk_creds_ignore_empty():
    assert attendee_stack.meeting_sdk_credentials_for_attendee(
        sdk_client_id="",
        sdk_client_secret="secret",
        s2s_client_id="s2s",
    ) is None


def test_meeting_sdk_creds_reject_s2s_reuse():
    # Server-to-Server Client ID must never be Attendee's Meeting SDK key.
    assert attendee_stack.meeting_sdk_credentials_for_attendee(
        sdk_client_id="same-id",
        sdk_client_secret="sdk-secret",
        s2s_client_id="same-id",
    ) is None


def test_meeting_sdk_creds_accept_distinct_general_app():
    got = attendee_stack.meeting_sdk_credentials_for_attendee(
        sdk_client_id="sdk-app",
        sdk_client_secret="sdk-secret",
        s2s_client_id="s2s-app",
    )
    assert got == ("sdk-app", "sdk-secret")


def test_ensure_zoom_creds_skips_when_only_s2s(monkeypatch):
    monkeypatch.setattr(attendee_stack.settings, "attendee_base_url", "http://localhost:8002/api/v1")
    monkeypatch.setattr(attendee_stack.settings, "zoom_sdk_client_id", "")
    monkeypatch.setattr(attendee_stack.settings, "zoom_sdk_client_secret", "")
    monkeypatch.setattr(attendee_stack.settings, "zoom_client_id", "s2s-id")
    monkeypatch.setattr(attendee_stack.settings, "zoom_client_secret", "s2s-secret")
    run = MagicMock()
    monkeypatch.setattr(attendee_stack.subprocess, "run", run)
    assert attendee_stack.ensure_attendee_zoom_credentials() is False
    run.assert_not_called()
