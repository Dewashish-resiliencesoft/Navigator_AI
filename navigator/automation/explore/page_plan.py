"""PAGE PLAN: look at a page once, decide the whole demo-worthy sequence.

`reason.choose_next` picks one element at a time and, because it prefers clear
navigation, produces a tab tour: click Inbox, click Kanban, click Contacts. That
is a sitemap, not a demo. This module asks a vision model the different question
-- "you are a demo host standing on this screen, what would you *do* here?" --
and gets back an ordered sequence that includes in-page work: open the create
modal, fill it, expand a row, apply a filter.

The plan is a suggestion, exactly like `reason.Choice`. Every action still goes
through the guardrail in the executor before anything is dispatched, and actions
marked `commit` are recorded but deliberately never executed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from navigator.automation.explore.session import element_key

#: Actions the planner may propose. "commit" is the mutating final click of a
#: form (Save / Send / Pay) -- recorded for the demo script, never run by the bot.
ACTION_KINDS = frozenset({"click", "fill", "commit"})

_PROMPT = """You are a product demo host looking at one screen of a web product.

Your job is to plan what you would DO on THIS screen to show it off to a
prospect -- not to list links to other pages. A tour of nav tabs is a bad demo.
A good demo opens a dialog, fills a realistic example, expands a row, applies a
filter, and explains what the prospect is seeing.

Current page: {url}
Pages already covered (do not plan another visit to these): {visited}
{extra}
Interactive elements on this screen (choose by index):
{elements}

Rules:
- Plan the actions IN ORDER, as a host would perform them.
- "click" opens something (a dialog, a row, a filter, a section).
- "fill" enters a value into an input.
- "commit" is a button that would SAVE, SEND, PAY, DELETE, INVITE or PUBLISH
  real data. Mark those as "commit" and never as "click". Include at most one.
- Only include an action if a prospect would find it interesting. Skip
  logout, cookie banners, and settings trivia.
- Prefer 3 to 8 actions. Fewer is fine on a sparse screen.

Reply with JSON only:
{{"purpose": "<one line: what this screen is for>",
  "actions": [{{"index": <int>, "kind": "click"|"fill"|"commit",
                "why": "<short>",
                "narration": "<one sentence the host says while doing it>",
                "demo_worthy": true|false}}]}}"""


@dataclass(frozen=True)
class PageAction:
    element_index: int
    kind: str
    why: str = ""
    narration: str = ""
    demo_worthy: bool = True


@dataclass(frozen=True)
class PagePlan:
    purpose: str = ""
    actions: tuple[PageAction, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return bool(self.actions)


def format_elements(elements: Sequence[dict[str, Any]]) -> str:
    """Numbered inventory. Same shape `reason.format_elements` produces."""
    lines = []
    for i, el in enumerate(elements):
        desc = (
            el.get("label") or el.get("text") or el.get("aria_label")
            or el.get("title") or el.get("name") or el.get("testid") or ""
        )
        kind = "fill" if el.get("fillable") else "click"
        lines.append(f"[{i}] {kind} <{el.get('tag')}> {desc!r} key={element_key(el)}")
    return "\n".join(lines) or "(none)"


def plan_page(
    *,
    url: str,
    elements: Sequence[dict[str, Any]],
    screenshot_b64: str,
    ask_vision: Callable[[str, str], str] | None,
    ask_text: Callable[[str], str] | None = None,
    visited_paths: Sequence[str] = (),
    focus_hint: str = "",
    corrections: Sequence[str] = (),
) -> PagePlan:
    """One plan for one screen. Empty plan means "fall back to reason.choose_next".

    Vision is preferred because the screenshot is what tells the model that the
    "+" in the corner opens a create dialog. Text-only is a usable fallback.
    """
    if not elements:
        return PagePlan()

    extra_bits: list[str] = []
    if focus_hint.strip():
        extra_bits.append(
            f"The client asked you to focus on: {focus_hint.strip()!r}."
        )
    if corrections:
        extra_bits.append(
            "Known issues from previous runs (do not repeat these):\n"
            + "\n".join(f"- {c}" for c in corrections)
        )

    prompt = _PROMPT.format(
        url=url,
        visited=", ".join(visited_paths) if visited_paths else "(none yet)",
        extra=("\n".join(extra_bits) + "\n") if extra_bits else "",
        elements=format_elements(elements),
    )

    raw = ""
    if ask_vision is not None and screenshot_b64:
        try:
            raw = ask_vision(prompt, screenshot_b64)
        except Exception as exc:  # noqa: BLE001
            if "stopped by client" in str(exc).lower():
                raise
            print(f"[explore] page plan vision failed: {exc}", flush=True)
    if not raw and ask_text is not None:
        try:
            raw = ask_text(prompt)
        except Exception as exc:  # noqa: BLE001
            if "stopped by client" in str(exc).lower():
                raise
            print(f"[explore] page plan text failed: {exc}", flush=True)

    return parse_plan(raw, len(elements))


def parse_plan(raw: str, element_count: int) -> PagePlan:
    """Tolerant JSON extraction. Anything unusable yields an empty plan."""
    match = re.search(r"\{.*\}", (raw or "").strip(), re.S)
    if not match:
        return PagePlan()
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return PagePlan()
    if not isinstance(data, dict):
        return PagePlan()

    actions: list[PageAction] = []
    seen: set[int] = set()
    for item in data.get("actions") or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < element_count or idx in seen:
            continue
        kind = str(item.get("kind") or "click").strip().lower()
        if kind not in ACTION_KINDS:
            kind = "click"
        seen.add(idx)
        actions.append(
            PageAction(
                element_index=idx,
                kind=kind,
                why=str(item.get("why") or "").strip()[:200],
                narration=str(item.get("narration") or "").strip()[:300],
                demo_worthy=bool(item.get("demo_worthy", True)),
            )
        )
    # One commit per screen: a plan that saves twice is a plan that invented a
    # second form, and running a mutating click on a guess is the worst outcome.
    kept: list[PageAction] = []
    commit_used = False
    for action in actions:
        if action.kind == "commit":
            if commit_used:
                continue
            commit_used = True
        kept.append(action)

    return PagePlan(
        purpose=str(data.get("purpose") or "").strip()[:300],
        actions=tuple(kept),
    )
