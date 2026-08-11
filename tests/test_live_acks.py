"""Working acks while EXECUTING runs browser tools."""

from __future__ import annotations

from dataclasses import replace

from navigator.agent.nodes.executing import executing
from navigator.agent.nodes.planning import planning
from navigator.voice.live_acks import (
    maybe_nudge_live,
    next_working_ack,
    reset_nudge_throttle_for_tests,
)


class FakeLive:
    def __init__(self) -> None:
        self.nudges: list[str] = []
        self.context: list[str] = []

    def nudge(self, text: str) -> None:
        self.nudges.append(text)

    def add_context(self, text: str) -> None:
        self.context.append(text)


def test_next_working_ack_rotates_english():
    reset_nudge_throttle_for_tests()
    a = next_working_ack("en")
    b = next_working_ack("en")
    assert a and b
    assert "…" in a or a.endswith("...")


def test_hindi_acks_are_localized():
    text = next_working_ack("hi")
    assert any(tok in text.lower() for tok in ("haan", "second", "hmm", "theek", "dekh"))


def test_throttle_blocks_rapid_nudges():
    reset_nudge_throttle_for_tests()
    live = FakeLive()
    assert maybe_nudge_live(live, language="en") is True
    assert maybe_nudge_live(live, language="en") is False
    assert len(live.nudges) == 1


def test_executing_nudges_live_before_tool(state, deps):
    reset_nudge_throttle_for_tests()
    live = FakeLive()
    deps = replace(deps, live_agent=live, spoken_language="en")
    state.update(planning(state, deps))
    executing(state, deps)
    assert live.nudges, "must speak a working ack before the browser action"
