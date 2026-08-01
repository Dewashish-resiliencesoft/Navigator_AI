"""JOINING: connect to the meeting when CallDeps carries an Attendee client.

Standalone demos (no meeting_url) stay a no-op transcript line.
Live Meet demos usually join earlier in live_demo; this node covers graph-driven joins.
"""

from __future__ import annotations

import time

from navigator.agent.state import CallDeps, CallState


def joining(state: CallState, deps: CallDeps) -> CallState:
    if not deps.meeting_url or deps.attendee is None:
        return CallState(transcript=["[joined call: standalone mode, no meeting]"])

    # Already joined upstream (live_demo) — don't create a second bot.
    if deps.bot_id:
        return CallState(transcript=[f"[joined call: bot {deps.bot_id}]"])

    client = deps.attendee
    persona = deps.graph.effective_persona()
    bot = client.join(
        deps.meeting_url,
        bot_name=persona.agent_name,
        voice_agent_url=deps.voice_agent_url,
        reserve_voice_agent=deps.voice_agent_url is None,
    )
    deps.bot_id = bot.id
    deadline = time.time() + 180
    while time.time() < deadline:
        current = client.get(bot.id)
        if current.state == "joined":
            return CallState(transcript=[f"[joined call: bot {bot.id}]"])
        if current.state == "fatal_error":
            raise RuntimeError(f"Attendee bot {bot.id} fatal_error")
        time.sleep(2)
    raise TimeoutError(f"Attendee bot {bot.id} did not join in time")
