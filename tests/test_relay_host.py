"""Screenshare relay bind host. No listening sockets — ``start`` is a fake."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from navigator.core.settings import settings
from navigator.meeting.relay_host import (
    DEFAULT_ATTENDEE_DOCKER_NETWORK,
    docker_network_gateway,
    local_screenshare_url,
    open_screenshare_relay,
)


class _Proc:
    def __init__(self, returncode: int, stdout: str):
        self.returncode = returncode
        self.stdout = stdout


def _handle(host: str) -> SimpleNamespace:
    return SimpleNamespace(host=host, port=4321)


def test_docker_network_override(monkeypatch):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["timeout"] = kwargs.get("timeout")
        return _Proc(0, "172.20.0.1\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert docker_network_gateway("attendee-fork_attendee_network") == "172.20.0.1"
    assert seen["cmd"][3] == "attendee-fork_attendee_network"
    assert seen["timeout"] == 5


def test_docker_network_missing_is_none(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, ""))
    assert docker_network_gateway(DEFAULT_ATTENDEE_DOCKER_NETWORK) is None


def test_docker_network_empty_gateway_is_none(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "<no value>\n"))
    assert docker_network_gateway("attendee_attendee_network") is None


def test_bind_host_override_skips_gateway(monkeypatch):
    monkeypatch.setattr(settings, "relay_bind_host", "127.0.0.1")
    monkeypatch.setattr(settings, "relay_public_host", "")
    calls: list[str] = []

    def start(*, host, meeting_url):
        calls.append(host)
        assert meeting_url == "https://zoom.us/j/1"
        return _handle(host)

    relay, url_host = open_screenshare_relay(
        meeting_url="https://zoom.us/j/1",
        gateway="172.18.0.1",
        start=start,
    )
    assert calls == ["127.0.0.1"]
    assert url_host == "127.0.0.1"
    assert relay.port == 4321


def test_gateway_bind_is_the_url_host(monkeypatch, capsys):
    monkeypatch.setattr(settings, "relay_bind_host", "")
    monkeypatch.setattr(settings, "relay_public_host", "")

    def start(*, host, meeting_url):
        assert host == "172.18.0.1"
        return _handle(host)

    _relay, url_host = open_screenshare_relay(
        meeting_url="",
        gateway="172.18.0.1",
        start=start,
    )
    assert url_host == "172.18.0.1"
    assert capsys.readouterr().out == ""


def test_public_host_is_the_url_host(monkeypatch):
    monkeypatch.setattr(settings, "relay_bind_host", "")
    monkeypatch.setattr(settings, "relay_public_host", "host.docker.internal")

    def start(*, host, meeting_url):
        return _handle(host)

    _relay, url_host = open_screenshare_relay(
        meeting_url="",
        gateway="172.18.0.1",
        start=start,
    )
    assert url_host == "host.docker.internal"
    assert local_screenshare_url(url_host, 4321) == "http://host.docker.internal:4321/view"


def test_gateway_eaddrnotavail_falls_back(monkeypatch, capsys):
    monkeypatch.setattr(settings, "relay_bind_host", "")
    monkeypatch.setattr(settings, "relay_public_host", "10.0.2.2")
    calls: list[str] = []

    def start(*, host, meeting_url):
        calls.append(host)
        if host == "172.18.0.1":
            raise OSError(99, "Cannot assign requested address")
        return _handle(host)

    _relay, url_host = open_screenshare_relay(
        meeting_url="",
        gateway="172.18.0.1",
        start=start,
    )
    assert calls == ["172.18.0.1", "0.0.0.0"]
    assert url_host == "10.0.2.2"
    err = capsys.readouterr().out
    assert "listening on 0.0.0.0" in err
    assert "NAVIGATOR_RELAY_PUBLIC_HOST is unset" not in err


def test_fallback_without_public_host_warns(monkeypatch, capsys):
    monkeypatch.setattr(settings, "relay_bind_host", "")
    monkeypatch.setattr(settings, "relay_public_host", "")
    monkeypatch.setattr(settings, "attendee_docker_network", "attendee-fork_attendee_network")

    def start(*, host, meeting_url):
        assert host == "0.0.0.0"
        return _handle(host)

    _relay, url_host = open_screenshare_relay(
        meeting_url="",
        gateway=None,
        start=start,
    )
    assert url_host is None
    err = capsys.readouterr().out
    assert "attendee-fork_attendee_network" in err
    assert "NAVIGATOR_RELAY_PUBLIC_HOST" in err
    assert "host.docker.internal" in err
    assert "10.0.2.2" in err


def test_other_oserror_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(settings, "relay_bind_host", "")
    monkeypatch.setattr(settings, "relay_public_host", "")
    calls: list[str] = []

    def start(*, host, meeting_url):
        calls.append(host)
        raise OSError(98, "Address already in use")

    with pytest.raises(OSError, match="already in use"):
        open_screenshare_relay(meeting_url="", gateway="172.18.0.1", start=start)
    assert calls == ["172.18.0.1"]


def test_screenshare_local_off_does_not_bind_wildcard(monkeypatch, capsys):
    monkeypatch.setattr(settings, "relay_bind_host", "")
    monkeypatch.setattr(settings, "relay_public_host", "10.0.2.2")
    calls: list[dict] = []

    def start(**kwargs):
        calls.append(kwargs)
        return _handle(kwargs.get("host", "127.0.0.1"))

    _relay, url_host = open_screenshare_relay(
        meeting_url="https://meet.google.com/abc",
        gateway=None,
        local_enabled=False,
        start=start,
    )
    assert url_host is None
    assert calls == [{"meeting_url": "https://meet.google.com/abc"}]
    assert capsys.readouterr().out == ""
