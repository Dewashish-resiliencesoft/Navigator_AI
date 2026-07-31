"""JOINING: connect to the meeting when CallDeps carries an Attendee client.

Standalone demos (no meeting_url) stay a no-op transcript line.
"""

from __future__ import annotations

import time

from navigator.agent.state import CallDeps, CallState


def joining(state: CallState, deps: CallDeps) -> CallState:
    if not deps.meeting_url or deps.attendee is None:
        return CallState(transcript=["[joined call: standalone mode, no meeting]"])

    client = deps.attendee
    bot = client.join(
        deps.meeting_url,
        bot_name="Navigator AI",
        voice_agent_url=deps.voice_agent_url,
    )
    deadline = time.time() + 120
    while time.time() < deadline:
        current = client.get(bot.id)
        if current.state == "joined":
            return CallState(
                transcript=[f"[joined call: bot {bot.id}]"],
            )
        if current.state == "fatal_error":
            raise RuntimeError(f"Attendee bot {bot.id} fatal_error")
        time.sleep(2)
    raise TimeoutError(f"Attendee bot {bot.id} did not join in time")
