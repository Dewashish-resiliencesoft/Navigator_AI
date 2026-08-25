from __future__ import annotations

import pytest

from navigator.meeting import screenshare
from navigator.meeting.screenshare import arm_screenshare, wait_until_screenshare_live


@pytest.fixture(autouse=True)
def _skip_attendee_dns_probe(monkeypatch):
    monkeypatch.setattr(screenshare, "verify_attendee_docker_dns", lambda _host: None)


class FakeTunnelWait:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str, *, timeout_s: float = 30.0) -> None:
        self.urls.append(url)


class FakeAttendee:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def enable_screenshare(self, bot_id: str, screenshare_url: str) -> None:
        self.calls.append((bot_id, screenshare_url))


class FakeRelay:
    def __init__(self) -> None:
        self.frame_hits = 0
        self.view_hits = 0


def test_arm_screenshare_reprobes_then_patches():
    wait = FakeTunnelWait()
    client = FakeAttendee()
    arm_screenshare(
        client=client,
        bot_id="b1",
        public_view="https://x.trycloudflare.com/view",
        wait_until_public=wait,
    )
    assert wait.urls == ["https://x.trycloudflare.com/view"]
    assert client.calls == [("b1", "https://x.trycloudflare.com/view")]


def test_arm_screenshare_does_not_patch_if_probe_fails():
    def boom(url: str, *, timeout_s: float = 30.0) -> None:
        raise RuntimeError("not reachable")

    client = FakeAttendee()
    try:
        arm_screenshare(
            client=client,
            bot_id="b1",
            public_view="https://dead.example/view",
            wait_until_public=boom,
        )
        assert False, "expected raise"
    except RuntimeError:
        pass
    assert client.calls == []


def test_wait_until_screenshare_live_sees_frame_hits():
    relay = FakeRelay()
    pushes = {"n": 0}

    def push() -> None:
        pushes["n"] += 1
        # Simulate Attendee polling after a few paints.
        if pushes["n"] >= 3:
            relay.frame_hits += 5

    ok = wait_until_screenshare_live(
        relay,
        push_frame=push,
        baseline_frame_hits=0,
        min_frame_hits=10,
        timeout_s=2.0,
        settle_s=0.05,
    )
    assert ok is True
    assert pushes["n"] >= 3


def test_wait_until_screenshare_live_timeout():
    relay = FakeRelay()
    ok = wait_until_screenshare_live(
        relay,
        push_frame=lambda: None,
        baseline_frame_hits=0,
        min_frame_hits=50,
        timeout_s=0.3,
        settle_s=0.0,
    )
    assert ok is False
