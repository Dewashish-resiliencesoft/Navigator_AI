"""Live Meet demo — skipped unless NAVIGATOR_MEET_LIVE=1."""

from __future__ import annotations

import pytest

from navigator.settings import settings

pytestmark = pytest.mark.skipif(
    not settings.meet_live,
    reason="set NAVIGATOR_MEET_LIVE=1 for live Meet test",
)


def test_bot_joins_meet_and_runs_demo_graph():
    from navigator.meeting.live_demo import run_live_meet_demo

    bot_id = run_live_meet_demo(
        headful=True,
        mute=True,
        interactive_listen=False,
        open_meet_in_browser=False,
        wait_for_human=False,
    )
    assert bot_id
