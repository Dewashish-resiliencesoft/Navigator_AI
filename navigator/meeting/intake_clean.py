"""Clean raw STT answers into fields safe for spoken templates."""

from __future__ import annotations

import re

_NAME_PREFIX = re.compile(
    r"^(?:hi|hello|hey)[,.\s]+|"
    r"^(?:my name is|i am|i'm|this is)\s+",
    re.I,
)


def clean_name(raw: str) -> str:
    t = " ".join((raw or "").strip().split())
    if not t:
        return ""
    # Repeat strip — "hello my name is X"
    for _ in range(3):
        nxt = _NAME_PREFIX.sub("", t).strip(" .,!")
        if nxt == t:
            break
        t = nxt
    t = re.split(r"[.!,]|\band\b", t, maxsplit=1)[0].strip()
    return t.title() if t else ""


def clean_phrase(raw: str, *, max_len: int = 120) -> str:
    t = " ".join((raw or "").strip().split()).strip(" .,")
    return t[:max_len]
