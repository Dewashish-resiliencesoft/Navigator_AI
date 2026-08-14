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
        "thank you",
        "thanks",
        "there",
        "friend",
    }
)
# Bot self-intro / platform chrome — STT often echoes these as the "prospect name".
_RESERVED_NAME_DEFAULTS = frozenset(
    {
        "navigator",
        "navigator ai",
        "navigatorai",
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
_DECLINE = re.compile(
    r"\b("
    r"don'?t know|do not know|not sure|no idea|"
    r"don'?t have(?: one)?|do not have(?: one)?|"
    r"no company|without company|"
    r"none|n/?a|nothing|skip|prefer not"
    r")\b",
    re.I,
)


def is_likely_bot_echo(heard: str, bot_text: str) -> bool:
    """True when STT likely captured the bot's own TTS, not the prospect."""

    def _norm(s: str) -> str:
        s = " ".join((s or "").lower().split())
        return re.sub(r"[^a-z0-9\s]", "", s)

    def _words(s: str) -> set[str]:
        return {w for w in _norm(s).split() if len(w) > 1}

    h = _norm(heard)
    b = _norm(bot_text)
    if len(h) < 3 or not b:
        return False
    if h in b or b in h:
        return True
    hw, bw = _words(heard), _words(bot_text)
    if not hw:
        return False
    if len(hw) < 2:
        return next(iter(hw)) in bw or h in b
    return len(hw & bw) / len(hw) >= 0.65


def is_declined(raw: str) -> bool:
    """True when prospect opts out of answering (skip field in pitch)."""
    return bool(_DECLINE.search((raw or "").strip()))


def clean_name(raw: str, *, reserved: frozenset[str] | None = None) -> str:
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
    blocked = set(_RESERVED_NAME_DEFAULTS)
    if reserved:
        blocked.update(r.strip().lower() for r in reserved if r and r.strip())
    low = re.sub(r"[^a-z0-9\s]", "", t.lower()).strip()
    low_compact = low.replace(" ", "")
    for ban in blocked:
        ban_n = re.sub(r"[^a-z0-9\s]", "", ban).strip()
        ban_c = ban_n.replace(" ", "")
        if not ban_n:
            continue
        if low == ban_n or low_compact == ban_c:
            return ""
        # "Hi Navigator AI thanks" already reduced, or name == agent display name
        if ban_n in low or low in ban_n:
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
