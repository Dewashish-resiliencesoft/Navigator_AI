"""Prospect-facing speech: never tech jargon or secrets in the meeting."""

from __future__ import annotations

import itertools
import re

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

REFUSE_SPOKEN = (
    "I can't share credentials, secrets, or technical internals — "
    "that's on us to keep safe. Happy to keep showing you the product though."
)


def prospect_safe_line(line: str) -> str:
    text = line or ""
    if _TECH.search(text) or _SECRET.search(text):
        return next(_soft_cycle)
    return text


def is_exfil_request(utterance: str) -> bool:
    return bool(_EXFIL.search(utterance or ""))
