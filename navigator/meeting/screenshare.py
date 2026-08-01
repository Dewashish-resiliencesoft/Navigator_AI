"""Arm Attendee screenshare only after the public relay URL is reachable.

Also wait until Attendee is actually pulling /frame.jpg before the demo
walkthrough starts — otherwise Meet shows a blank/late share.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from navigator.meeting.tunnel import wait_until_public as _default_wait


class _Attendee(Protocol):
    def enable_screenshare(self, bot_id: str, screenshare_url: str) -> None: ...


class _Relay(Protocol):
    frame_hits: int
    view_hits: int


def arm_screenshare(
    *,
    client: _Attendee,
    bot_id: str,
    public_view: str,
    wait_until_public: Callable[..., None] | None = None,
    timeout_s: float = 45.0,
) -> None:
    wait = wait_until_public or _default_wait
    print(f"[live] tunnel_ready=probing url={public_view}", flush=True)
    wait(public_view, timeout_s=timeout_s)
    print(f"[live] tunnel_ready=ok url={public_view}", flush=True)
    client.enable_screenshare(bot_id, public_view)
    print(f"[live] screenshare_patch=ok bot={bot_id}", flush=True)


def wait_until_screenshare_live(
    relay: _Relay,
    *,
    push_frame: Callable[[], None],
    baseline_frame_hits: int | None = None,
    min_frame_hits: int = 10,
    timeout_s: float = 90.0,
    settle_s: float = 2.0,
) -> bool:
    """Block until Attendee is polling /frame.jpg, keeping the start page painted.

    Returns True if live pulls observed; False on timeout (caller may still proceed).
    """
    start_hits = (
        relay.frame_hits if baseline_frame_hits is None else baseline_frame_hits
    )
    deadline = time.monotonic() + timeout_s
    print(
        f"[live] waiting for Meet to pull screenshare frames "
        f"(need +{min_frame_hits} fetches)…",
        flush=True,
    )
    while time.monotonic() < deadline:
        try:
            push_frame()
        except Exception as exc:  # noqa: BLE001
            print(f"[live] frame push while waiting: {exc}", flush=True)
        got = relay.frame_hits - start_hits
        if got >= min_frame_hits:
            print(
                f"[live] screenshare_live=ok frame_hits=+{got} "
                f"view_hits={relay.view_hits}",
                flush=True,
            )
            # Keep painting while Meet settles on the stream.
            settle_deadline = time.monotonic() + settle_s
            while time.monotonic() < settle_deadline:
                try:
                    push_frame()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.2)
            return True
        time.sleep(0.25)
    print(
        f"[live] screenshare_live=timeout "
        f"frame_hits=+{relay.frame_hits - start_hits} "
        f"(continuing anyway — Meet may still catch up)",
        flush=True,
    )
    return False
