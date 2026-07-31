"""LISTENING: wait for a participant to finish speaking, then transcribe.

CI / scripted: fixed utterance.
interactive_listen: stdin.
audio_frames set: Silero VAD + Groq Whisper (or injected transcribe_audio);
falls back to stdin/scripted if no utterance arrives.

When the utterance looks like a correction of the last action, flag
``user_correction`` so PLANNING skips Playwright and logs a pending rule.
"""

from __future__ import annotations

from navigator.agent.nodes.reflecting import classify_correction
from navigator.agent.state import CallDeps, CallState
from navigator.settings import settings
from navigator.voice.stt import VoiceSegmenter, transcribe

SCRIPTED_UTTERANCE = "Can you show me how sending a message works?"


def listening(state: CallState, deps: CallDeps) -> CallState:
    utterance = _capture_utterance(deps)
    last = _last_entry(state, deps)
    is_correction = False
    if last is not None:
        try:
            is_correction = classify_correction(
                utterance,
                last,
                api_key=deps.groq_api_key
                if deps.groq_api_key is not None
                else settings.groq_api_key or None,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[listen] classify_correction skipped: {exc}", flush=True)

    return CallState(
        transcript=[f"user: {utterance}"],
        user_correction=is_correction,
    )


def _capture_utterance(deps: CallDeps) -> str:
    if deps.audio_frames is not None:
        text = _from_audio(deps)
        if text:
            return text
        print("[listen] no Meet audio utterance — falling back", flush=True)

    if deps.interactive_listen:
        print(
            "\n[listen] Prospect is speaking in Meet. "
            "Type what they asked for (or Enter for default demo):\n"
            f"         default: {SCRIPTED_UTTERANCE!r}",
            flush=True,
        )
        try:
            typed = input("[listen] > ").strip()
        except EOFError:
            typed = ""
        return typed or SCRIPTED_UTTERANCE

    return SCRIPTED_UTTERANCE


def _from_audio(deps: CallDeps) -> str:
    assert deps.audio_frames is not None
    segmenter = VoiceSegmenter()
    for pcm in segmenter.segments(deps.audio_frames):
        if deps.transcribe_audio is not None:
            return deps.transcribe_audio(pcm) or ""
        api_key = (
            deps.groq_api_key
            if deps.groq_api_key is not None
            else settings.groq_api_key
        )
        if not api_key:
            print("[listen] no Groq key for STT", flush=True)
            return ""
        return transcribe(pcm, api_key)
    return ""


def _last_entry(state: CallState, deps: CallDeps):
    entries = list(state.get("entries") or [])
    if entries:
        return entries[-1]
    try:
        rows = deps.log.entries(state["session_id"], product_id=deps.product_id)
    except Exception:  # noqa: BLE001
        return None
    return rows[-1] if rows else None
