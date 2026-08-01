"""Clean raw STT answers into fields safe for spoken templates."""

from __future__ import annotations

import re

_NAME_PREFIX = re.compile(
    r"^(?:hi|hello|hey)[,.\s]+|"
    r"^(?:my name is|i am|i'm|this is)\s+",
    re.I,
)
_FILLER_NAMES = frozenset(
    {
        "yeah",
        "yes",
        "yep",
        "yup",
        "ok",
        "okay",
        "hi",
        "hello",
        "hey",
        "um",
        "uh",
        "so",
        "well",
    }
)
_COMPANY_PREFIX = re.compile(
    r"^(?:i(?:'m| am)|we(?:'re| are)|they(?:'re| are))\s+"
    r"(?:with|at|from)\s+|"
    r"^(?:i|we|they)\s+work(?:ing)?\s+(?:at|for|with)\s+",
    re.I,
)
_BIZ_PREFIX = re.compile(
    r"^(?:we(?:'re| are)|i(?:'m| am)|it(?:'s| is))\s+"
    r"(?:in\s+(?:a|an)\s+|a\s+|an\s+)?",
    re.I,
)
_NEED_FILLER = re.compile(
    r"^(?:yeah|yes|yep|so|um|uh|well|actually|like|okay|ok)[,.\s]+",
    re.I,
)
_NEED_LEAD = re.compile(
    r"^(?:we(?:'re| are)|i(?:'m| am)|we need|i need|looking for|need)\s+",
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
    if not t or t.lower() in _FILLER_NAMES:
        return ""
    return t.title()


def clean_phrase(raw: str, *, max_len: int = 120) -> str:
    t = " ".join((raw or "").strip().split()).strip(" .,")
    return t[:max_len]


def clean_company(raw: str) -> str:
    t = clean_phrase(raw, max_len=80)
    if not t:
        return ""
    for _ in range(2):
        nxt = _COMPANY_PREFIX.sub("", t).strip(" .,")
        if nxt == t:
            break
        t = nxt
    return t


def clean_business(raw: str) -> str:
    t = clean_phrase(raw, max_len=90)
    if not t:
        return ""
    for _ in range(2):
        nxt = _BIZ_PREFIX.sub("", t).strip(" .,")
        if nxt == t:
            break
        t = nxt
    return t


def summarize_need(raw: str, *, max_len: int = 80) -> str:
    """Short spoken need — strip fillers, prefer the CRM/product clause."""
    t = " ".join((raw or "").strip().split()).strip(" .,")
    if not t:
        return ""
    for _ in range(4):
        nxt = _NEED_FILLER.sub("", t).strip(" .,")
        if nxt == t:
            break
        t = nxt
    # Prefer the clause that names the product need.
    lower = t.lower()
    for marker in ("whatsapp", "crm", "inbox", "automat", "contact", "lead"):
        idx = lower.find(marker)
        if idx > 0:
            # Back up to sentence/clause start
            chunk = t[idx:]
            # Include a bit of context before marker if short lead-in
            start = t.rfind(".", 0, idx)
            start = start + 1 if start >= 0 else max(0, idx - 20)
            # Prefer "We need WhatsApp…" style if present
            need_at = lower.rfind("need", 0, idx + 1)
            if need_at >= 0 and idx - need_at < 40:
                start = need_at
            t = t[start:].strip(" .,")
            break
    for _ in range(2):
        nxt = _NEED_LEAD.sub("", t).strip(" .,")
        if nxt == t:
            break
        t = nxt
    if len(t) > max_len:
        cut = t[:max_len].rsplit(" ", 1)[0]
        t = cut.rstrip(".,;:") if cut else t[:max_len]
    return t
