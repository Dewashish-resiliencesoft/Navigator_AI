"""Goodbye detection and silence D helpers for end-of-call policy."""

from __future__ import annotations

import re

_GOODBYE = re.compile(
    r"\b(no|nope|nothing|done|goodbye|bye|thanks|thank you|that's all|"
    r"that is all|i'm good|im good|all set|end the meeting|end meeting|"
    r"end the demo|stop the demo|leave the (call|meeting)|hang up)\b",
    re.I,
)

ANYTHING_ELSE = "That's the walkthrough — any questions before we wrap up?"
WRAP_UP = "I think everything is clear. I'll leave the call now."
OFF_TOPIC = (
    "I can only cover this product in the demo. Anything else you'd like to see here?"
)
SILENCE_S = 60.0
#: After a question detour, wait this long for a reply before auto-resuming.
RESUME_SILENCE_S = 10.0
QUESTION_ANSWERED = (
    "I think that answers your question — does that help, or is there anything else?"
)
RESUME_AFTER_QUESTION = (
    "Great — let's pick up the demo where we left off."
)
RESUME_AFTER_SILENCE = (
    "I think that covers it — let's continue from where we were. "
    "Feel free to ask me anytime."
)


def is_goodbye(utterance: str) -> bool:
    return bool(_GOODBYE.search(utterance or ""))


def next_silence_action(*, silence_rounds: int) -> str:
    """First silent wait after Q&A prompt → wrap. ``silence_rounds`` kept for callers."""
    return "leave"
