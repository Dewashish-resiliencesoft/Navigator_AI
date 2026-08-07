"""LISTENING: wait for a participant to finish speaking, then transcribe.

CI / scripted: fixed utterance.
interactive_listen: stdin.
audio_frames set: Silero VAD + Groq Whisper (or injected transcribe_audio);
falls back to stdin/scripted if no utterance arrives.

When the utterance looks like a correction of the last action, flag
``user_correction`` so PLANNING skips Playwright and logs a pending rule.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from navigator.agent.end_policy import SILENCE_S
from navigator.agent.nodes.reflecting import classify_correction
from navigator.agent.state import CallDeps, CallState
from navigator.core.settings import settings
from navigator.voice.stt import VoiceSegmenter, transcribe
from navigator.voice.language import sync_call_language

SCRIPTED_UTTERANCE = "Can you show me how sending a message works?"


def _aborted(deps: CallDeps) -> bool:
    ev = deps.stop_event
    return ev is not None and getattr(ev, "is_set", lambda: False)()


def _want_classify(deps: CallDeps) -> bool:
    # Playlist / auto-advance: corrections rare; Groq classify after every
    # utterance adds hundreds of ms before the next reply.
    if getattr(deps, "playlist_only", False) or getattr(deps, "strict_playlist", False):
        return False
    if getattr(deps, "auto_advance_walkthrough", False):
        return False
    return True


def listening(state: CallState, deps: CallDeps) -> CallState:
    if _aborted(deps) or getattr(deps.speaker, "bot_ended", False):
        return CallState(finished=True, phase="ending")
    if deps.set_status is not None:
        deps.set_status("listening", "Listening…")
    if deps.set_avatar_state is not None:
        deps.set_avatar_state("listening")
    # Prefer utterance captured during barge-in over a fresh listen wait.
    pending = deps.pending_barge_in
    if pending:
        utterance = pending.pop(0).strip()
        if utterance:
            print(f"[listen] barge-in utterance: {utterance!r}", flush=True)
            sync_call_language(deps, utterance)
            last = _last_entry(state, deps)
            is_correction = False
            if last is not None and _want_classify(deps):
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

    utterance = _capture_utterance(state, deps)
    if utterance:
        sync_call_language(deps, utterance)
        print(f"[listen] heard: {utterance!r}", flush=True)
    if utterance and deps.on_user_utterance is not None:
        try:
            deps.on_user_utterance(utterance)
        except Exception as exc:  # noqa: BLE001
            print(f"[listen] on_user_utterance skipped: {exc}", flush=True)
    last = _last_entry(state, deps)
    is_correction = False
    if last is not None and _want_classify(deps):
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


def _capture_utterance(state: CallState, deps: CallDeps) -> str:
    phase = state.get("phase") or ""
    anything_else = phase == "anything_else"
    walkthrough = phase == "walkthrough"
    awaiting_resume = phase == "awaiting_resume"

    if walkthrough and getattr(deps, "auto_advance_walkthrough", False):
        from navigator.voice.language import (
            detect_language_switch,
            poll_barge_in_language_switch,
            sync_call_language,
        )

        poll_barge_in_language_switch(deps)
        if deps.audio_frames is not None:
            text = _from_audio(deps, silence_timeout=2.5)
            if text:
                sync_call_language(deps, text)
                if detect_language_switch(text) is not None:
                    return text
        return ""

    if deps.audio_frames is not None:
        try:
            if phase == "awaiting_resume":
                from navigator.agent.brain_config import pacing_resume_silence
                from navigator.agent.end_policy import RESUME_SILENCE_S

                cfg = getattr(deps, "brain_config", None)
                pacing = "neutral"
                mem = getattr(deps, "memory", None)
                if mem is not None and getattr(mem, "pacing_history", None):
                    pacing = mem.pacing_history[-1]
                timeout = (
                    pacing_resume_silence(pacing, cfg)
                    if cfg is not None
                    else RESUME_SILENCE_S
                )
            elif walkthrough:
                cfg = getattr(deps, "brain_config", None)
                timeout = cfg.listen_timeout_s if cfg is not None else 12.0
            elif anything_else:
                from navigator.agent.end_policy import SILENCE_S

                timeout = SILENCE_S
            else:
                timeout = None
            text = _from_audio(deps, silence_timeout=timeout)
            if text:
                return text
            if anything_else or walkthrough or awaiting_resume:
                # Empty → planning advances walkthrough or runs silence end-policy.
                return ""
            print("[listen] no Meet audio utterance — falling back", flush=True)
        except RuntimeError as exc:
            # Missing silero-vad/torch — do not invent a fake user question.
            print(f"[listen] STT unavailable ({exc}) — falling back", flush=True)
            if (anything_else or walkthrough or awaiting_resume) and not deps.interactive_listen:
                return ""

    if deps.interactive_listen:
        print(
            "\n[listen] Prospect is speaking in Meet. "
            "Type what they asked for (or Enter to continue walkthrough):\n"
            f"         empty Enter = continue; or ask something allowed.",
            flush=True,
        )
        try:
            typed = input("[listen] > ").strip()
        except EOFError:
            typed = ""
        if typed:
            return typed
        return "" if (anything_else or walkthrough or awaiting_resume) else SCRIPTED_UTTERANCE

    if anything_else or walkthrough or awaiting_resume:
        return ""
    return SCRIPTED_UTTERANCE


def _frames_until_deadline(
    frames: Iterator[bytes], deadline_s: float
) -> Iterator[bytes]:
    """Yield PCM frames until the deadline; stop early so silence policy can run."""
    deadline = time.monotonic() + deadline_s
    for frame in frames:
        if time.monotonic() >= deadline:
            return
        yield frame


def _from_audio(deps: CallDeps, *, silence_timeout: float | None = None) -> str:
    assert deps.audio_frames is not None
    frames: Iterator[bytes] = deps.audio_frames
    if silence_timeout is not None:
        frames = _frames_until_deadline(deps.audio_frames, silence_timeout)
    segmenter = VoiceSegmenter(min_silence_ms=settings.live_stt_min_silence_ms)
    for pcm in segmenter.segments(frames):
        if _aborted(deps) or getattr(deps.speaker, "bot_ended", False):
            return ""
        if deps.transcribe_audio is not None:
            text = deps.transcribe_audio(pcm) or ""
        else:
            api_key = (
                deps.groq_api_key
                if deps.groq_api_key is not None
                else settings.groq_api_key
            )
            if not api_key:
                print("[listen] no Groq key for STT", flush=True)
                return ""
            text = transcribe(pcm, api_key) or ""
        text = text.strip()
        if not text:
            continue
        if deps.is_bot_echo is not None and deps.is_bot_echo(text):
            print(f"[listen] ignoring bot echo: {text!r}", flush=True)
            continue
        return text
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
