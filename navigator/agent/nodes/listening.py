"""LISTENING: wait for a participant to finish speaking, then transcribe.

CI / scripted: fixed utterance.
interactive_listen: stdin.
audio_frames set: Silero VAD + Groq Whisper (or injected transcribe_audio);
falls back to stdin/scripted if no utterance arrives.
"""

from __future__ import annotations

from navigator.agent.state import CallDeps, CallState
from navigator.settings import settings
from navigator.voice.stt import VoiceSegmenter, transcribe

SCRIPTED_UTTERANCE = "Can you show me how sending a message works?"


def listening(state: CallState, deps: CallDeps) -> CallState:
    if deps.audio_frames is not None:
        utterance = _from_audio(deps)
        if utterance:
            return CallState(transcript=[f"user: {utterance}"])
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
        utterance = typed or SCRIPTED_UTTERANCE
        return CallState(transcript=[f"user: {utterance}"])

    return CallState(transcript=[f"user: {SCRIPTED_UTTERANCE}"])


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
