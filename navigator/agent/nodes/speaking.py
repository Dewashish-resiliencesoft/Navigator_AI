"""SPEAKING: narrate whatever is queued, then clear the queue.

The single owner of TTS. Nodes upstream queue lines into state["narration"] and
append their own transcript entries; this node only turns text into audio, so
"what was said" and "what was heard out loud" can't drift apart.
"""

from __future__ import annotations

import time

from navigator.agent.speech_safety import prospect_safe_line
from navigator.agent.state import CLEAR, CallDeps, CallState

_FRAME_RETRY_S = 0.12


def _ensure_frame_fresh(deps: CallDeps, last_hits: int | None) -> int | None:
    """One cheap retry when the relay has not advanced since the last beat."""
    if deps.push_frame is None or deps.get_frame_hits is None:
        return last_hits
    hits = deps.get_frame_hits()
    if last_hits is not None and hits <= last_hits:
        deps.push_frame()
        time.sleep(_FRAME_RETRY_S)
        hits = deps.get_frame_hits()
    return hits


def speaking(state: CallState, deps: CallDeps) -> CallState:
    ev = deps.stop_event
    if (ev is not None and getattr(ev, "is_set", lambda: False)()) or getattr(
        deps.speaker, "bot_ended", False
    ):
        return CallState(narration=CLEAR, finished=True, phase="ending")
    if deps.set_status is not None:
        deps.set_status("speaking", "Speaking…")
    if deps.set_avatar_state is not None:
        deps.set_avatar_state("speaking")
    interrupted = False
    last_hits: int | None = None
    for line in state.get("narration") or []:
        if interrupted:
            break
        if getattr(deps.speaker, "bot_ended", False):
            return CallState(narration=CLEAR, finished=True, phase="ending")
        last_hits = _ensure_frame_fresh(deps, last_hits)
        if deps.push_frame is not None:
            deps.push_frame()
        safe = prospect_safe_line(line)
        if not (safe or "").strip():
            continue
        if safe != line:
            print(f"[speak] scrubbed technical narration: {line!r}", flush=True)
        deps.speaker.say(safe)
        if getattr(deps.speaker, "interrupted", False):
            interrupted = True
        if deps.push_frame is not None:
            deps.push_frame()
        if deps.get_frame_hits is not None:
            last_hits = deps.get_frame_hits()
    if getattr(deps.speaker, "bot_ended", False):
        return CallState(narration=CLEAR, finished=True, phase="ending")
    if deps.set_avatar_state is not None:
        deps.set_avatar_state("idle")
    return CallState(narration=CLEAR)
