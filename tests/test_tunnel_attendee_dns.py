"""Attendee Docker must resolve tunnel hostnames (screenshare NXDOMAIN guard)."""

from __future__ import annotations

import subprocess

import pytest

from navigator.meeting import tunnel


def test_verify_attendee_docker_dns_skips_without_container(monkeypatch):
    monkeypatch.setattr(tunnel, "_attendee_webpage_streamer_container", lambda: None)
    monkeypatch.setattr(tunnel, "_attendee_worker_container", lambda: None)
    tunnel.verify_attendee_docker_dns("fresh-name.trycloudflare.com")


def test_verify_attendee_docker_dns_raises_when_exec_fails(monkeypatch):
    monkeypatch.setattr(tunnel, "_attendee_worker_container", lambda: None)
    monkeypatch.setattr(
        tunnel, "_attendee_webpage_streamer_container", lambda: "attendee-streamer-1"
    )

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "docker")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="cannot resolve"):
        tunnel.verify_attendee_docker_dns("fresh-name.trycloudflare.com")
