from __future__ import annotations

from navigator.meeting.screenshare import arm_screenshare


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
