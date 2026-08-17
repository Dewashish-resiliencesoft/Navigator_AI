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


def _authored_lines(graph: object) -> set[str]:
    """Narration the Client wrote into the site graph.

    ponytail: recomputed per line. A site graph has a handful of flows and this
    runs once per spoken sentence, so caching it would cost more to get right
    (SiteGraph is neither hashable nor weak-referenceable) than it saves.
    """
    out: set[str] = set()
    for page in getattr(graph, "pages", {}).values():
        for flow_id in getattr(page, "flows", {}):
            try:
                lines = graph.flow_narration_lines(flow_id)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                continue
            out.update((x or "").strip() for x in lines if (x or "").strip())
    return out


def _last_user_text(state: CallState) -> str:
    for line in reversed(state.get("transcript") or []):
        if isinstance(line, str) and line.startswith("user:"):
            return line[5:].strip()
    return ""


def _say_mode(deps: CallDeps, line: str) -> str:
    """Client-authored copy is spoken verbatim; anything we generated may flex.

    Narration written into the site graph is the Client's own wording and often
    contractual, so it goes out word for word. Lines PLANNING phrased at runtime
    are already ours, so letting Live deliver them naturally costs nothing.
    """
    if deps.graph is None:
        return "verbatim"
    return "verbatim" if (line or "").strip() in _authored_lines(deps.graph) else "natural"


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
    # Keep lead-in narration queued until EXECUTING starts cursor/action.
    if state.get("pending_calls"):
        return CallState()
    prior = state.get("pre_action_speech")
    if prior is not None and hasattr(prior, "wait"):
        prior.wait(timeout=120.0)
    live = deps.live_agent
    # Live already answered conversational Q&A out loud. Re-saying the
    # planner's reply is a second voice on the same question. Walkthrough
    # lines still go out — those arrive with pending_calls or authored copy.
    if (
        live is not None
        and _last_user_text(state)
        and not state.get("pending_calls")
    ):
        authored = _authored_lines(deps.graph) if deps.graph is not None else set()
        queued = [prospect_safe_line(x) for x in (state.get("narration") or [])]
        if not any((x or "").strip() in authored for x in queued if x):
            print("[speak] skip replay — Live already answered", flush=True)
            return CallState(narration=CLEAR)

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
        if live is not None:
            live.say(safe, mode=_say_mode(deps, line))
            if getattr(live, "interrupted", False):
                # The prospect talked over us and Live is answering them. Let
                # that exchange finish; the walkthrough step does not advance,
                # so the next pass repeats from here.
                from navigator.core.settings import settings

                live.wait_until_idle(silence_s=settings.live_resume_silence_s)
                interrupted = True
        else:
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
    return CallState(narration=CLEAR, pre_action_speech=None)
