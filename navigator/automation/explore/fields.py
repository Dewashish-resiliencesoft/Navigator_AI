"""Form-field classification: can the explorer invent a value, or must it ask?

Guessable-safe means a generic placeholder is both harmless and realistic
(an email, a person's name, a date). Business-specific means only the Client
knows the answer (a billing code, an internal project id, a tax rate) -- filling
those with a guess produces a demo that shows wrong data as if it were real.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from navigator.automation.explore.guardrail import element_text_blob

#: (regex over label/name/type/autocomplete) -> placeholder value.
#:
#: Every alternative is word-bounded on purpose. An unanchored `region` matches
#: "Regional tax rate" and quietly fills a business-specific field with
#: "California" -- exactly the guess this module exists to avoid.
_SAFE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bemail\b|\be-mail\b", "demo.user@example.com"),
    (r"\bphone\b|\btel\b|\bmobile\b", "555-0142"),
    (r"\bfirst\s*name\b|\bgiven\s*name\b|\bfname\b", "Alex"),
    (r"\blast\s*name\b|\bsurname\b|\bfamily\s*name\b|\blname\b", "Morgan"),
    (r"\bfull\s*name\b|\byour\s*name\b|\bname\b", "Alex Morgan"),
    (r"\bcompany\b|\borgani[sz]ation\b|\bbusiness\s*name\b", "Example Corp"),
    (r"\bcity\b", "Springfield"),
    (r"\bstate\b|\bprovince\b|\bregion\b", "California"),
    (r"\bcountry\b", "United States"),
    (r"\bzip\b|\bzipcode\b|\bpostal\b", "94105"),
    (r"\baddress\b|\bstreet\b", "100 Market Street"),
    (r"\bsearch\b|\bquery\b|\bfilter\b", "test"),
    (r"\bdate\b|\bdob\b|\bbirth\w*\b", "2026-01-15"),
    (r"\btime\b", "10:00"),
    (r"\bquantity\b|\bqty\b|\bcount\b|\bnumber\s*of\b", "2"),
    (r"\btitle\b|\bsubject\b|\blabel\b", "Demo item"),
    (
        r"\bdescri\w*\b|\bnotes?\b|\bcomments?\b|\bmessage\b",
        "Added during an automated demo walkthrough.",
    ),
    (r"\busername\b|\bhandle\b|\buser\s*id\b", "demo.user"),
    (r"\burl\b|\bwebsite\b|\blink\b", "https://example.com"),
)

_INPUT_TYPE_DEFAULTS: dict[str, str] = {
    "email": "demo.user@example.com",
    "tel": "555-0142",
    "date": "2026-01-15",
    "time": "10:00",
    "number": "2",
    "url": "https://example.com",
    "search": "test",
}


@dataclass(frozen=True)
class FieldPlan:
    classification: str  # guessable_safe | business_specific
    value: str
    reason: str


_JUDGE_PROMPT = """An automated product-demo explorer must fill this form field
on a live product. Decide whether a generic placeholder is acceptable, or
whether only the product's owner could know a correct value.

GUESSABLE means generic personal/demo data is fine (name, email, city, date,
quantity, search term, free-text note).
BUSINESS_SPECIFIC means the value is domain knowledge unique to this company
(billing/account codes, internal ids, SKUs, tax rates, pricing, API keys,
regulated identifiers). If unsure, answer BUSINESS_SPECIFIC.

Field:
{field}

Reply with JSON only:
{{"classification": "guessable"|"business_specific", "value": "<placeholder if guessable, else empty>", "reason": "<short>"}}"""


def _pattern_value(el: dict[str, Any]) -> tuple[str, str] | None:
    input_type = str(el.get("type") or "").lower()
    if input_type == "password":
        return None  # never guessed; the vault sentinel path handles these
    blob = element_text_blob(el).lower()
    autocomplete = str(el.get("autocomplete") or "").lower()
    haystack = f"{blob} {autocomplete}".strip()
    for pattern, value in _SAFE_PATTERNS:
        if re.search(pattern, haystack):
            return value, f"matched {pattern!r}"
    if input_type in _INPUT_TYPE_DEFAULTS:
        return _INPUT_TYPE_DEFAULTS[input_type], f"input type={input_type}"
    return None


def classify_field(
    el: dict[str, Any],
    *,
    judge: Callable[[str], str] | None = None,
) -> FieldPlan:
    """Decide how (or whether) to fill a field.

    Unlike the destructive guardrail, an unavailable judge here degrades to
    *asking the client* rather than blocking -- pausing for a human answer is
    always safe, it just costs time.
    """
    hit = _pattern_value(el)
    if hit is not None:
        value, reason = hit
        return FieldPlan("guessable_safe", value, reason)

    if judge is None:
        return FieldPlan("business_specific", "", "no judge configured — asking client")

    try:
        raw = judge(_JUDGE_PROMPT.format(field=_describe(el)))
    except Exception as exc:  # noqa: BLE001
        return FieldPlan("business_specific", "", f"judge unavailable: {exc}")

    plan = _parse(raw)
    if plan is None:
        return FieldPlan("business_specific", "", "unparseable judge reply")
    return plan


def _describe(el: dict[str, Any]) -> str:
    keep = ("tag", "type", "name", "id", "testid", "label", "aria_label", "text", "autocomplete")
    return "\n".join(f"{k}: {el[k]}" for k in keep if el.get(k))


def _parse(raw: str) -> FieldPlan | None:
    import json

    match = re.search(r"\{.*\}", (raw or "").strip(), re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    kind = str(data.get("classification") or "").strip().lower()
    reason = str(data.get("reason") or "").strip()
    if kind == "guessable":
        value = str(data.get("value") or "").strip()
        if not value:
            return FieldPlan("business_specific", "", "judge gave no placeholder")
        return FieldPlan("guessable_safe", value, reason or "judged guessable")
    if kind == "business_specific":
        return FieldPlan("business_specific", "", reason or "judged business-specific")
    return None


def question_for(el: dict[str, Any]) -> str:
    label = (
        el.get("label") or el.get("aria_label") or el.get("text")
        or el.get("name") or el.get("id") or "this field"
    )
    return f'What should I enter for "{label}"?'
