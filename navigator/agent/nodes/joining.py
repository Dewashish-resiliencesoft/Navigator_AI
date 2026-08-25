"""JOINING: connect to the meeting when CallDeps carries an Attendee client.

Standalone demos (no meeting_url) stay a no-op transcript line.
Live Meet demos usually join earlier in live_demo; this node covers graph-driven joins.
"""

from __future__ import annotations

import time

from navigator.agent.state import CallDeps, CallState
from navigator.meeting.zoom_host import is_zoom_meeting, zoom_zak_callback_url
from navigator.core.settings import settings


def joining(state: CallState, deps: CallDeps) -> CallState:
    if not deps.meeting_url or deps.attendee is None:
        return CallState(transcript=["[joined call: standalone mode, no meeting]"])

    # Already joined upstream (live_demo) — don't create a second bot.
    if deps.bot_id:
        return CallState(transcript=[f"[joined call: bot {deps.bot_id}]"])

    client = deps.attendee
    persona = deps.graph.effective_persona()
    zoom_tokens_url = None
    if is_zoom_meeting(deps.meeting_url):
        zoom_tokens_url = zoom_zak_callback_url()
    bot = client.join(
        deps.meeting_url,
        bot_name=(persona.agent_name or "Navigator AI").strip() or "Navigator AI",
        voice_agent_url=deps.voice_agent_url,
        reserve_voice_agent=deps.voice_agent_url is None,
        google_meet_use_login=settings.google_meet_use_login,
        zoom_tokens_url=zoom_tokens_url,
        zoom_sdk="web" if zoom_tokens_url else None,
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
