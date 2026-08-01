"""Goodbye detection and silence D helpers for end-of-call policy."""

from __future__ import annotations

import re

_GOODBYE = re.compile(
    r"\b(no|nope|nothing|done|goodbye|bye|thanks|thank you|that's all|"
    r"that is all|i'm good|im good|all set)\b",
    re.I,
)

ANYTHING_ELSE = "Anything else you'd like to see before we wrap up?"
WRAP_UP = "Thanks for your time — I'll leave the call now. Take care!"
SILENCE_S = 30.0


def is_goodbye(utterance: str) -> bool:
    return bool(_GOODBYE.search(utterance or ""))


def next_silence_action(*, silence_rounds: int) -> str:
    """0 → reask once; ≥1 → leave."""
    return "reask" if silence_rounds <= 0 else "leave"
