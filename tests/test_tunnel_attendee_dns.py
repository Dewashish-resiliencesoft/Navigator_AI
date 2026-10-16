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

    def run(args, **kwargs):
        # Inspect must report the container running. A failure there is skipped
        # on purpose; only a failed `docker exec` is the DNS error.
        if args and len(args) > 1 and args[1] == "inspect":
            return subprocess.CompletedProcess(args, 0, stdout="true\n", stderr="")
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="cannot resolve"):
        tunnel.verify_attendee_docker_dns("fresh-name.trycloudflare.com")
