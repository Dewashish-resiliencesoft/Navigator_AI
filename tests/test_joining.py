"""JOINING node."""

from __future__ import annotations

from unittest.mock import MagicMock

from navigator.agent.nodes.joining import joining
from navigator.agent.state import CallDeps, initial_state
from navigator.voice.tts import PrintSpeaker
from uuid import uuid4


def test_joining_standalone_when_no_meeting(deps, state):
    out = joining(state, deps)
    assert "standalone" in out["transcript"][0]


def test_joining_skips_when_bot_already_set(site_graph, page, log, tmp_path):
    deps = CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        archive_dir=tmp_path / "a",
        meeting_url="https://meet.google.com/abc",
        attendee=MagicMock(),
        bot_id="bot_existing",
    )
    out = joining(initial_state(uuid4(), "inbox"), deps)
    assert "bot_existing" in out["transcript"][0]
    deps.attendee.join.assert_not_called()
