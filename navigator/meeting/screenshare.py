"""Arm Attendee screenshare only after the public relay URL is reachable."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from navigator.meeting.tunnel import wait_until_public as _default_wait


class _Attendee(Protocol):
    def enable_screenshare(self, bot_id: str, screenshare_url: str) -> None: ...


def arm_screenshare(
    *,
    client: _Attendee,
    bot_id: str,
    public_view: str,
    wait_until_public: Callable[..., None] | None = None,
    timeout_s: float = 30.0,
) -> None:
    wait = wait_until_public or _default_wait
    print(f"[live] tunnel_ready=probing url={public_view}", flush=True)
    wait(public_view, timeout_s=timeout_s)
    print(f"[live] tunnel_ready=ok url={public_view}", flush=True)
    client.enable_screenshare(bot_id, public_view)
    print(f"[live] screenshare_patch=ok bot={bot_id}", flush=True)
