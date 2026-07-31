"""LISTENING: wait for a participant to finish speaking, then transcribe.

STUB. Phase 1 has no audio input; it hands PLANNING a fixed prompt so the scripted
flow has something to react to.
"""

from __future__ import annotations

from navigator.agent.state import CallDeps, CallState

SCRIPTED_UTTERANCE = "Can you show me how sending a message works?"


def listening(state: CallState, deps: CallDeps) -> CallState:
    # TODO(phase 2): Silero VAD over the Attendee audio stream to find end-of-speech
    # (150-250ms frames, not 30ms -- Silero prefers larger windows), then POST the
    # segment to Groq whisper-large-v3-turbo. Free tier allows 7200 audio-sec/hour,
    # enough for one concurrent call. See navigator/voice/stt.py.
    return CallState(transcript=[f"user: {SCRIPTED_UTTERANCE}"])
