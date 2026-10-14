"""Prospect-facing speech: never tech jargon or secrets in the meeting."""

from __future__ import annotations

import itertools
import re

from navigator.core.schemas import Persona

_SOFT = (
    "Oh — something glitched on our side there, not yours. "
    "It's nothing you did. We're sorting it; I'll keep going.",
    "Hmm, a small hiccup on our end — not anything you did. "
    "We're on it; I'll continue.",
    "Looks like a little snag on our side. You're all good — "
    "we'll fix that and keep going.",
)
_soft_cycle = itertools.cycle(_SOFT)

_TECH = re.compile(
    r"Page\.(click|fill|goto|wait)|Timeout \d+ms|action failed:|locator\(|"
    r"didn't do what I expected|stack trace|Traceback|selector=|"
    r"playwright|waiting for.*(selector|locator)",
    re.I,
)
_SECRET = re.compile(
    r"(?:password|passwd|api[_-]?key|secret|token|bearer|authorization)"
    r"\s*[=:]\s*\S+"
    r"|Bearer\s+[A-Za-z0-9._\-]+"
    r"|sk-[A-Za-z0-9]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----",
    re.I,
)
_EXFIL = re.compile(
    r"\b("
    r"api[_ -]?key|password|passwd|secret|credentials?|env(ironment)? vars?|"
    r"stack trace|exact error|raw error|show (me )?(the )?logs?|"
    r"access token|private key"
    r")\b",
    re.I,
)
# Platform / operator chrome must never be spoken to End Users on a live Meet.
_PLATFORM = re.compile(
    r"\b(client dashboard|ops console|navigator (ai )?dashboard|"
    r"configure this in the client)\b",
    re.I,
)
_PLACEHOLDER_PRODUCT = frozenset(
    {
        "your product",
        "recorded product",
        "recorded draft",
        "product",
        "client",
    }
)

REFUSE_SPOKEN = (
    "I can't share credentials, secrets, or technical internals — "
    "that's on us to keep safe. Happy to keep showing you the product though."
)


_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEXID = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{12,}\b", re.I)
_SLUG = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")
_SLUG_PREFIX = re.compile(r"^(page|flow|step|feature|section|btn|nav|tab)_", re.I)


def _humanize_slug(m: re.Match) -> str:
    raw = _SLUG_PREFIX.sub("", m.group(0))
    return raw.replace("_", " ").strip()


def humanize_ids(text: str) -> str:
    """Never read internal ids aloud: page_automation_integrations -> automation integrations."""
    text = _UUID.sub("", text or "")
    text = _HEXID.sub("", text)
    return _SLUG.sub(_humanize_slug, text)


def prospect_safe_line(line: str) -> str:
    text = humanize_ids(line or "")
    if not text.strip():
        return ""
    if _TECH.search(text) or _SECRET.search(text):
        return next(_soft_cycle)
    # Drop platform-operator phrases rather than soft-fail the whole line.
    cleaned = _PLATFORM.sub("", text)
    cleaned = re.sub(r"\s*,\s*,+", ",", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    # Trim orphan commas left by phrase deletion — never strip sentence periods.
    cleaned = re.sub(r"^[,;:\-—]+\s*", "", cleaned)
    cleaned = re.sub(r"\s*[,;:\-—]+$", "", cleaned)
    return cleaned if cleaned else next(_soft_cycle)


def is_exfil_request(utterance: str) -> bool:
    return bool(_EXFIL.search(utterance or ""))


def prospect_facing_persona(
    persona: Persona, *, fallback_product: str = ""
) -> Persona:
    """Strip placeholders / Platform copy before Meet speech."""
    name = (persona.product_name or "").strip()
    one = (persona.one_liner or "").strip()
    if name.lower() in _PLACEHOLDER_PRODUCT:
        fb = (fallback_product or "").strip()
        if fb and fb.lower() not in _PLACEHOLDER_PRODUCT:
            name = fb.replace("-", " ").replace("_", " ").strip()
            if name.islower():
                name = name.title()
        else:
            name = "this product"
    if _PLATFORM.search(one) or one.lower().startswith("recorded draft"):
        one = ""
    if persona.product_name == name and persona.one_liner == one:
        return persona
    return persona.model_copy(update={"product_name": name, "one_liner": one})
