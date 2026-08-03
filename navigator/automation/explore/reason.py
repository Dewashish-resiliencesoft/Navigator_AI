"""REASON: pick the next element to try, from the unvisited set only.

Groq (llama-3.3-70b-versatile, same model the live planner uses) does the
routine choice. The vision model is escalated to only when the text inventory
is ambiguous -- an icon-only toolbar, say, where labels are empty.

The choice returned here is a *suggestion*. The guardrail runs again in the
executor before anything is dispatched, so nothing this module returns can
cause a destructive action on its own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from navigator.automation.explore.session import element_key

MODEL = "llama-3.3-70b-versatile"

_PROMPT = """You are exploring a product's web UI to build a guided demo.

Goal: find the interesting, user-facing capabilities a salesperson would show a
prospect. Prefer navigation and views that reveal core functionality. Avoid
settings/admin trivia, logout, and anything that looks like it changes data.

Current page: {url}
{corrections}
Elements you have NOT tried yet (choose one by index):
{elements}

Reply with JSON only:
{{"index": <int>, "why": "<short reason>", "narration": "<one sentence a demo host would say while doing this>"}}"""


@dataclass(frozen=True)
class Choice:
    index: int
    why: str
    narration: str


def format_elements(elements: Sequence[dict[str, Any]]) -> str:
    lines = []
    for i, el in enumerate(elements):
        desc = (
            el.get("label") or el.get("text") or el.get("aria_label")
            or el.get("title") or el.get("name") or el.get("testid") or ""
        )
        kind = "fill" if el.get("fillable") else "click"
        lines.append(f"[{i}] {kind} <{el.get('tag')}> {desc!r} key={element_key(el)}")
    return "\n".join(lines) or "(none)"


def needs_vision(elements: Sequence[dict[str, Any]]) -> bool:
    """True when the text inventory is too thin to choose from.

    Icon-only UIs produce elements with no label, text, or aria-label; a text
    model cannot meaningfully rank those, so a screenshot is worth the cost.
    """
    if not elements:
        return False
    unlabeled = sum(
        1
        for el in elements
        if not (el.get("label") or el.get("text") or el.get("aria_label") or el.get("title"))
    )
    return unlabeled / len(elements) >= 0.6


def choose_next(
    *,
    url: str,
    elements: Sequence[dict[str, Any]],
    corrections: Sequence[str] = (),
    ask_text: Callable[[str], str] | None = None,
    ask_vision: Callable[[str, str], str] | None = None,
    screenshot: str = "",
) -> Choice | None:
    """Pick one element. None means "no usable choice" -> caller stops/backtracks."""
    if not elements:
        return None

    corr_block = ""
    if corrections:
        corr_block = (
            "Known issues from previous runs (do not repeat these):\n"
            + "\n".join(f"- {c}" for c in corrections)
            + "\n"
        )
    prompt = _PROMPT.format(
        url=url, corrections=corr_block, elements=format_elements(elements)
    )

    raw = ""
    if needs_vision(elements) and ask_vision is not None and screenshot:
        try:
            raw = ask_vision(prompt, screenshot)
        except Exception as exc:  # noqa: BLE001
            print(f"[explore] vision reason failed: {exc}", flush=True)
            raw = ""
    if not raw and ask_text is not None:
        try:
            raw = ask_text(prompt)
        except Exception as exc:  # noqa: BLE001
            print(f"[explore] text reason failed: {exc}", flush=True)
            raw = ""

    choice = _parse(raw, len(elements))
    if choice is not None:
        return choice
    # No model, or an unusable reply: fall back to the first untried element so
    # exploration still makes progress instead of stalling on a bad LLM day.
    return Choice(0, "fallback: first untried element", "")


def _parse(raw: str, count: int) -> Choice | None:
    match = re.search(r"\{.*\}", (raw or "").strip(), re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        idx = int(data.get("index"))
    except (TypeError, ValueError):
        return None
    if not 0 <= idx < count:
        return None
    return Choice(
        idx,
        str(data.get("why") or "").strip()[:200],
        str(data.get("narration") or "").strip()[:300],
    )
