"""SPEAKING: narrate whatever is queued, then clear the queue.

The single owner of TTS. Nodes upstream queue lines into state["narration"] and
append their own transcript entries; this node only turns text into audio, so
"what was said" and "what was heard out loud" can't drift apart.
"""

from __future__ import annotations

import time

from navigator.agent.speech_safety import prospect_safe_line
from navigator.agent.state import CLEAR, CallDeps, CallState
from navigator.agent.utterance import item_id, item_text, stamp_narration

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


def _say_mode(deps: CallDeps, line: str) -> str:  # noqa: ARG001
    """Demo narration always uses natural mode.

    Gemini Live's "word for word" verbatim instruction overrides the model's
    prosody and produces robotic, choppy, monotone delivery. Natural mode lets
    the model phrase the authored text with correct intonation, pauses, and
    rhythm while still delivering the same content.

    Intake Q&A uses "verbatim" separately (intake._say) to prevent the model
    from rephrasing intake questions into answers — that path is unaffected.
    """
    return "natural"


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
    from navigator.voice.conversation_language import publish_speech

    preview = prospect_safe_line(item_text((state.get("narration") or [""])[0]))
    publish_speech(deps, status="speaking", narration=preview)
    # Keep lead-in narration queued until EXECUTING starts cursor/action.
    if state.get("pending_calls"):
        return CallState()
    prior = state.get("pre_action_speech")
    if prior is not None and hasattr(prior, "wait"):
        prior.wait(timeout=120.0)
    live = deps.live_agent
    from navigator.voice.language import SWITCH_ACK

    queued_items = stamp_narration(state, state.get("narration") or [], kind="speak")
    queued_text = [prospect_safe_line(item_text(x)) for x in queued_items]
    # Live already answered conversational Q&A out loud. Re-saying the
    # planner's reply is a second voice on the same question. Walkthrough
    # lines still go out — those arrive with pending_calls or authored copy.
    # Language-switch acks are director-owned (Live must not also ack).
    switch_acks = set(SWITCH_ACK.values())
    if (
        live is not None
        and _last_user_text(state)
        and not state.get("pending_calls")
        and not any((x or "").strip() in switch_acks for x in queued_text if x)
    ):
        authored = _authored_lines(deps.graph) if deps.graph is not None else set()
        if not any((x or "").strip() in authored for x in queued_text if x):
            ids = [item_id(x) for x in queued_items if item_id(x)]
            print(
                f"[speak] skip replay — Live already answered ids={ids} "
                f"queue={len(queued_items)}",
                flush=True,
            )
            return CallState(narration=CLEAR, spoken_utterance_ids=ids)

    interrupted = False
    last_hits: int | None = None
    already = set(state.get("spoken_utterance_ids") or [])
    consumed: list[str] = []
    for item in queued_items:
        if interrupted:
            break
        if getattr(deps.speaker, "bot_ended", False):
            return CallState(
                narration=CLEAR,
                finished=True,
                phase="ending",
                spoken_utterance_ids=consumed,
            )
        uid = item_id(item)
        line = item_text(item)
        if uid and uid in already:
            print(f"[utterance] id={uid} skip replay queue={len(queued_items)}", flush=True)
            continue
        last_hits = _ensure_frame_fresh(deps, last_hits)
        if deps.push_frame is not None:
            deps.push_frame()
        safe = prospect_safe_line(line)
        if not (safe or "").strip():
            continue
        if safe != line:
            print(f"[speak] scrubbed technical narration: {line!r}", flush=True)
        print(
            f"[utterance] id={uid} tts_start queue={len(queued_items)}",
            flush=True,
        )
        snap = getattr(deps, "conversation_language", None)
        lang = getattr(snap, "narration_language", None) or getattr(
            deps, "spoken_language", None
        )
        if live is not None:
            if lang in ("en", "hi") and hasattr(live, "set_language"):
                live.set_language(lang)
            live.say(safe, mode=_say_mode(deps, line), utterance_id=uid or None)
            if getattr(live, "interrupted", False):
                from navigator.core.settings import settings

                live.wait_until_idle(silence_s=settings.live_resume_silence_s)
                interrupted = True
        else:
            try:
                deps.speaker.say(safe, language=lang)
            except TypeError:
                deps.speaker.say(safe)
            if getattr(deps.speaker, "interrupted", False):
                interrupted = True
        print(
            f"[utterance] id={uid} tts_end interrupted={interrupted}",
            flush=True,
        )
        if uid:
            consumed.append(uid)
            already.add(uid)
        if deps.push_frame is not None:
            deps.push_frame()
        if deps.get_frame_hits is not None:
            last_hits = deps.get_frame_hits()
    if getattr(deps.speaker, "bot_ended", False):
        return CallState(
            narration=CLEAR,
            finished=True,
            phase="ending",
            spoken_utterance_ids=consumed,
        )
    if deps.set_avatar_state is not None:
        deps.set_avatar_state("idle")
    return CallState(
        narration=CLEAR,
        pre_action_speech=None,
        spoken_utterance_ids=consumed,
    )
