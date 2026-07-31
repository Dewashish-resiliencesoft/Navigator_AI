"""JOINING: connect to the meeting, mic and camera off until the first utterance.

STUB. Phase 1 runs standalone against a local browser, so this is a no-op that
just records the transition.
"""

from __future__ import annotations

from navigator.agent.state import CallDeps, CallState


def joining(state: CallState, deps: CallDeps) -> CallState:
    # TODO(phase 3): deps.attendee.join(meeting_url, bot_name), poll /bots/<id>
    # until state == "joined", keep mic+cam muted, then start the media pipeline
    # (v4l2loopback + ffmpeg) that feeds the Playwright viewport into the bot's
    # outgoing video. See navigator/meeting/attendee.py.
    return CallState(transcript=["[joined call: standalone mode, no meeting]"])
