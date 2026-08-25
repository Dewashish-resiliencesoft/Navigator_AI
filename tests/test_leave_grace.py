"""Countdown after the prospect leaves the meeting, before ending the demo."""

from navigator.meeting.live_demo import next_leave_grace


def test_grace_starts_at_25_when_human_leaves():
    assert next_leave_grace(False, None) is None
    assert next_leave_grace(True, None) == 25


def test_grace_ticks_down_then_zero():
    assert next_leave_grace(True, 25) == 24
    assert next_leave_grace(True, 1) == 0
    assert next_leave_grace(True, 0) == 0


def test_rejoin_cancels_grace():
    assert next_leave_grace(False, 10) is None
