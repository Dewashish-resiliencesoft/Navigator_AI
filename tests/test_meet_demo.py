"""Live Meet demo — skipped unless NAVIGATOR_MEET_LIVE=1."""

from __future__ import annotations

import pytest

from navigator.settings import settings

pytestmark = pytest.mark.skipif(
    not settings.meet_live,
    reason="set NAVIGATOR_MEET_LIVE=1 for live Meet test",
)


def test_bot_joins_meet_and_teams_notified():
    from navigator.meeting.live_demo import run_live_meet_smoke

    bot_id = run_live_meet_smoke(hold_s=20.0, headful=True)
    assert bot_id
