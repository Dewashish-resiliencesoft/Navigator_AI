from pathlib import Path
from unittest.mock import MagicMock

import pytest

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


def test_compose_up_requires_files(tmp_path):
    with pytest.raises(FileNotFoundError, match="missing"):
        attendee_stack._docker_compose_up(tmp_path)
