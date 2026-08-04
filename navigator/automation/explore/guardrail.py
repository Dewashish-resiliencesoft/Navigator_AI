"""Destructive-action guardrail for autonomous exploration.

Two independent layers, both fail-closed:

1. Keyword heuristic over label / aria-label / text / name / value.
2. An LLM judgment pass for things the keyword list cannot know are mutating
   ("Archive this quarter", "Move to trash bin").

The guardrail is called from the executor immediately before dispatch -- NOT
from the reasoning prompt. A reasoning step that hallucinates or is manipulated
into proposing "delete workspace" still cannot execute it, because the decision
to run an action is made here and nowhere else.

Any failure of the judge (missing key, network error, unparseable answer) is
treated as flagged. Skipping a safe action costs a little coverage; executing a
destructive one costs the Client real data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

#: Matched as whole words against every text-ish attribute of a candidate.
DESTRUCTIVE_KEYWORDS: frozenset[str] = frozenset(
    {
        # Named in the product spec.
        "delete", "remove", "cancel", "unsubscribe", "send", "submit",
        "pay", "charge", "invite", "publish",
        # Same blast radius, different vocabulary.
        "destroy", "archive", "deactivate", "revoke", "transfer",
        "purchase", "checkout", "confirm", "erase", "wipe", "reset",
        "terminate", "suspend", "disable", "uninstall", "withdraw",
        "refund", "downgrade", "upgrade", "subscribe", "buy", "order",
    }
)

_TEXT_FIELDS = ("text", "label", "aria_label", "name", "value", "title", "alt")

_JUDGE_PROMPT = """You are a safety gate for an automated product-demo explorer.

The explorer is about to interact with this UI element on a live production
product. Decide whether activating it could CHANGE, SEND, DELETE, PUBLISH,
PURCHASE, or otherwise MUTATE real data or notify real people.

Read-only navigation (opening a page, a tab, a filter, a details view, an
expander, a sort control) is SAFE.
Anything that writes, sends, deletes, pays, invites, or publishes is DESTRUCTIVE.
If you are unsure, answer DESTRUCTIVE.

Element:
{element}

Reply with JSON only: {{"destructive": true|false, "reason": "<short reason>"}}"""


@dataclass(frozen=True)
class GuardrailVerdict:
    flagged: bool
    reason: str
    source: str  # keyword | llm | fail_closed | safe

    def as_dict(self) -> dict[str, Any]:
        return {"flagged": self.flagged, "reason": self.reason, "source": self.source}


@dataclass
class FlaggedAction:
    """One action the explorer refused to run, surfaced for client review."""

    label: str
    selector: str
    url: str
    reason: str
    source: str
    #: Same key `ExplorationSession.mark_tried` uses — for Allow → un-try.
    element_key: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "selector": self.selector,
            "url": self.url,
            "reason": self.reason,
            "source": self.source,
            "element_key": self.element_key,
        }


def element_text_blob(el: dict[str, Any]) -> str:
    parts = [str(el.get(f) or "") for f in _TEXT_FIELDS]
    return " ".join(p for p in parts if p).strip()


def keyword_hit(el: dict[str, Any]) -> str | None:
    """First destructive keyword found, or None.

    Word-boundary matching so "undelete" and "resend" do not silently pass while
    "Delete" and "Send" are caught -- and so "send" does not fire on "sender"
    when it appears inside a longer product noun.
    """
    blob = element_text_blob(el).lower()
    if not blob:
        return None
    for word in re.findall(r"[a-z]+", blob):
        if word in DESTRUCTIVE_KEYWORDS:
            return word
    return None


def looks_like_safe_nav(el: dict[str, Any]) -> bool:
    """True for obvious read-only navigation — skip the LLM judge (speed).

    Still fail-closed via keyword_hit before this is consulted.
    """
    if keyword_hit(el):
        return False
    tag = str(el.get("tag") or "").lower()
    role = str(el.get("role") or "").lower()
    href = str(el.get("href") or "").strip()
    if href.lower().startswith(("mailto:", "tel:", "javascript:")):
        return False
    if tag == "a" and href and href != "#":
        return True
    if role in {"link", "tab", "menuitem", "treeitem", "navigation"}:
        return True
    # Sidebar buttons often lack href but are labeled nav targets.
    blob = element_text_blob(el).lower()
    if not blob:
        return False
    navish = (
        "dashboard", "inbox", "kanban", "calendar", "settings", "reports",
        "home", "overview", "contacts", "chat", "pipeline", "board",
        "projects", "tasks", "messages", "profile", "billing", "team",
    )
    return any(w in blob for w in navish) and tag in {"button", "a", "div", "span", "li"}


def classify_action(
    el: dict[str, Any],
    *,
    judge: Callable[[str], str] | None = None,
) -> GuardrailVerdict:
    """Decide whether an element may be actioned. Fail-closed.

    `judge` takes a prompt and returns raw model text. None means no judge is
    configured, which is itself a fail-closed condition -- an explorer running
    without a judge only gets keyword protection, so it must not proceed on
    elements the keyword list cannot vouch for.
    """
    hit = keyword_hit(el)
    if hit:
        return GuardrailVerdict(True, f"keyword:{hit}", "keyword")

    # Fast path: clear navigation does not need a TPD-burning LLM round-trip.
    if looks_like_safe_nav(el):
        return GuardrailVerdict(False, "safe navigation heuristic", "nav_heuristic")

    if judge is None:
        return GuardrailVerdict(True, "no LLM judge configured", "fail_closed")

    try:
        raw = judge(_JUDGE_PROMPT.format(element=json.dumps(el, sort_keys=True)))
    except Exception as exc:  # noqa: BLE001
        if "stopped by client" in str(exc).lower():
            raise
        return GuardrailVerdict(True, f"judge unavailable: {exc}", "fail_closed")

    verdict = _parse_judge(raw)
    if verdict is None:
        return GuardrailVerdict(True, f"unparseable judge reply: {raw!r:.80}", "fail_closed")
    return verdict


def _parse_judge(raw: str) -> GuardrailVerdict | None:
    text = (raw or "").strip()
    if not text:
        return None
    # Models like to wrap JSON in prose or fences; take the first object.
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "destructive" not in data:
        return None
    destructive = data.get("destructive")
    if not isinstance(destructive, bool):
        return None
    reason = str(data.get("reason") or "").strip() or "judged destructive"
    if destructive:
        return GuardrailVerdict(True, reason, "llm")
    return GuardrailVerdict(False, reason or "judged safe", "safe")
