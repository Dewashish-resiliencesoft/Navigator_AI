"""SPEAKING: narrate whatever is queued, then clear the queue.

The single owner of TTS. Nodes upstream queue lines into state["narration"] and
append their own transcript entries; this node only turns text into audio, so
"what was said" and "what was heard out loud" can't drift apart.
"""

from __future__ import annotations

from navigator.agent.state import CLEAR, CallDeps, CallState


def speaking(state: CallState, deps: CallDeps) -> CallState:
    if getattr(deps.speaker, "bot_ended", False):
        return CallState(narration=CLEAR, finished=True, phase="ending")
    if deps.set_status is not None:
        deps.set_status("speaking", "Speaking…")
    interrupted = False
    for line in state.get("narration") or []:
        if interrupted:
            break
        if getattr(deps.speaker, "bot_ended", False):
            return CallState(narration=CLEAR, finished=True, phase="ending")
        if deps.push_frame is not None:
            deps.push_frame()
        deps.speaker.say(line)
        if getattr(deps.speaker, "interrupted", False):
            interrupted = True
        if deps.push_frame is not None:
            deps.push_frame()
    if getattr(deps.speaker, "bot_ended", False):
        return CallState(narration=CLEAR, finished=True, phase="ending")
    return CallState(narration=CLEAR)
