"""SPEAKING: narrate whatever is queued, then clear the queue.

The single owner of TTS. Nodes upstream queue lines into state["narration"] and
append their own transcript entries; this node only turns text into audio, so
"what was said" and "what was heard out loud" can't drift apart.
"""

from __future__ import annotations

from navigator.agent.state import CLEAR, CallDeps, CallState


def speaking(state: CallState, deps: CallDeps) -> CallState:
    for line in state.get("narration") or []:
        deps.speaker.say(line)
    return CallState(narration=CLEAR)
