"""What a step ACHIEVED, in one sentence, grounded in the before/after state.

A recorded step is `click_element` on alias `btn_create`. That is what was done,
not what it accomplished, and it is useless to a prospect and to flow ranking.
This module produces "Opens the invoice creation form" instead.

Two design rules, both learned from the failure modes:

  Diff the element inventory, not the DOM. Raw HTML diffs are dominated by
  framework churn -- re-generated class names, moved wrappers, changed keys. The
  inventory from `perceive.inventory()` is already the interactive surface, which
  is the only part a demo viewer can perceive.

  A loading spinner is not a change. If the only thing that appeared is a
  progress indicator, `has_meaningful_change` is False and no label is written.
  The alternative is a flow narrated as "Shows a loading spinner", which is worse
  than no narration. The caller re-labels after the page settles.

Every function here degrades to empty rather than raising. A missing label costs
a nicer demo; an exception costs the whole exploration run.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from navigator.automation.browser.screen_context import screen_snapshot

#: Elements that only report progress. Their arrival is not an achievement.
_SPINNER = re.compile(
    r"\b(loading|spinner|progress|skeleton|placeholder|shimmer|busy|pending)\b",
    re.I,
)

#: Cap on how much page text reaches the model. Two snapshots per label, so this
#: is doubled per call.
_TEXT_BUDGET = 400


@dataclass(frozen=True)
class StateSnapshot:
    """Page state at one instant, cheap enough to take twice per action."""

    url: str = ""
    title: str = ""
    text_hash: str = ""
    text: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"url": self.url, "title": self.title, "text_hash": self.text_hash}


def state_snapshot(page: Any, *, max_chars: int = _TEXT_BUDGET) -> StateSnapshot:
    """URL, title, and visible text for one moment.

    Built on `screen_snapshot()` rather than re-querying the page: that helper
    already handles the `inner_text` timeout and the `document.body.innerText`
    fallback, and it is the same view the live planner sees.
    """
    raw = ""
    try:
        raw = screen_snapshot(page, max_chars=max_chars)
    except Exception as exc:  # noqa: BLE001
        print(f"[semantics] snapshot failed: {exc}", flush=True)
        return StateSnapshot()

    url = title = text = ""
    for line in raw.splitlines():
        if line.startswith("url="):
            url = line[4:]
        elif line.startswith("title="):
            title = line[6:]
        elif line.startswith("visible="):
            text = line[8:]
    return StateSnapshot(
        url=url,
        title=title,
        text_hash=hashlib.sha256(text.encode()).hexdigest()[:16],
        text=text,
    )


def _label(el: dict[str, Any]) -> str:
    """Human-ish name for one inventory element."""
    for attr in ("text", "aria_label", "label", "title", "alt", "value"):
        val = str(el.get(attr) or "").strip()
        if val:
            return f"{el.get('role') or el.get('tag') or 'element'}: {val[:40]}"
    return str(el.get("tag") or "element")


def _keys(elements: list[dict[str, Any]]) -> dict[str, str]:
    """element_key -> readable label, for set arithmetic between two states."""
    from navigator.automation.explore.session import element_key

    out: dict[str, str] = {}
    for el in elements or []:
        if isinstance(el, dict):
            out.setdefault(element_key(el), _label(el))
    return out


@dataclass(frozen=True)
class StateDiff:
    """What changed between two page states, in viewer-perceivable terms."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    url_changed: bool = False
    text_changed: bool = False
    has_meaningful_change: bool = False

    @property
    def summary(self) -> str:
        parts: list[str] = []
        if self.url_changed:
            parts.append("URL changed")
        if self.added:
            parts.append(f"Added: {', '.join(self.added[:6])}")
        if self.removed:
            parts.append(f"Removed: {', '.join(self.removed[:6])}")
        if self.text_changed and not parts:
            parts.append("Page content changed")
        return "; ".join(parts) or "No visible change"


def diff_summary(
    before: StateSnapshot,
    after: StateSnapshot,
    elements_before: list[dict[str, Any]],
    elements_after: list[dict[str, Any]],
) -> StateDiff:
    """Compare two states. Spinner-only arrivals are not meaningful."""
    was, now = _keys(elements_before), _keys(elements_after)
    added = tuple(now[k] for k in now.keys() - was.keys())
    removed = tuple(was[k] for k in was.keys() - now.keys())

    url_changed = bool(before.url and after.url and before.url != after.url)
    text_changed = bool(
        before.text_hash and after.text_hash and before.text_hash != after.text_hash
    )

    # A spinner appearing means the page is still working, so hold the label.
    real_added = tuple(a for a in added if not _SPINNER.search(a))
    spinner_only = bool(added) and not real_added and not url_changed

    meaningful = (url_changed or bool(real_added) or bool(removed)) and not spinner_only
    # Text-only change counts (a field now holds a value) but is the weakest signal.
    if not meaningful and text_changed and not spinner_only:
        meaningful = True

    return StateDiff(
        added=added,
        removed=removed,
        url_changed=url_changed,
        text_changed=text_changed,
        has_meaningful_change=meaningful,
    )


_LABEL_PROMPT = """You are describing what one action achieved in a web app, for a
product demo narration. Ground every word in the observed change below. Do not
guess intent and do not invent features.

Action: {tool} on "{element}"
URL before: {before_url}
URL after: {after_url}
Title before: {before_title}
Title after: {after_title}
Observed change: {diff}
Visible text before: {before_text}
Visible text after: {after_text}

Rules:
- Describe what the action ACHIEVED, not what was clicked.
- Bad: "Clicked the blue button". Good: "Opens the campaign creation form".
- Bad: "Filled input". Good: "Enters the campaign name".
- If the URL changed, say where it went.
- If a form or modal appeared, say what it is for.
- One sentence, at most 15 words, present tense.
- If nothing meaningful changed, reply exactly: NO_CHANGE

Reply with the sentence only, no quotes."""

_MAX_WORDS = 15


def label_step(
    *,
    tool: str,
    element: str,
    before: StateSnapshot,
    after: StateSnapshot,
    diff: StateDiff,
    ask_text: Callable[[str], str] | None,
) -> str:
    """One sentence for what this step achieved. Empty string when unavailable.

    Empty covers every "we cannot say anything useful" case -- no model, nothing
    changed, the model said NO_CHANGE, or the call failed. Callers store a label
    only when it is non-empty, so absence never has to be special-cased.
    """
    if ask_text is None or not diff.has_meaningful_change:
        return ""

    prompt = _LABEL_PROMPT.format(
        tool=tool,
        element=element,
        before_url=before.url,
        after_url=after.url,
        before_title=before.title,
        after_title=after.title,
        diff=diff.summary,
        before_text=before.text[:_TEXT_BUDGET],
        after_text=after.text[:_TEXT_BUDGET],
    )
    try:
        raw = ask_text(prompt) or ""
    except Exception as exc:  # noqa: BLE001
        print(f"[semantics] label failed: {exc}", flush=True)
        return ""

    line = " ".join(raw.strip().splitlines()[:1] if raw.strip() else []).strip()
    line = line.strip("\"'").strip()
    if not line or line.upper().startswith("NO_CHANGE"):
        return ""
    words = line.split()
    if len(words) > _MAX_WORDS:
        line = " ".join(words[:_MAX_WORDS])
    return line


_FLOW_PROMPT = """Below are the steps of one demo flow through a web product,
already described in plain language.

{steps}

Give this flow:
1. a name, 3-5 words, verb-noun style
2. a one-sentence purpose
3. 4-8 lowercase keyword tags

Use only what the steps show. Do not invent features or brand names.

Reply with JSON only:
{{"name": "...", "purpose": "...", "tags": ["...", "..."]}}"""


@dataclass
class FlowSemantics:
    """Machine-drafted meaning for one flow. The Client edits this."""

    purpose: str = ""
    tags: tuple[str, ...] = ()
    auto_name: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "tags": list(self.tags),
            "auto_name": self.auto_name,
            "steps": self.steps,
        }

    def retrieval_text(self) -> str:
        """What flow ranking should match an utterance against.

        Feeds `knowledge.context.flow_text(trigger_intent=...)`, which is why the
        tags are inlined rather than kept structured: `score_flows` embeds one
        string per flow.
        """
        parts = [p for p in (self.purpose, " ".join(self.tags)) if p.strip()]
        return " — ".join(parts)


def label_flow(
    descriptions: list[str],
    *,
    ask_text: Callable[[str], str] | None,
) -> FlowSemantics:
    """Name, purpose, and tags for a flow, from its per-step labels."""
    usable = [d.strip() for d in descriptions if d and d.strip()]
    if not usable or ask_text is None:
        return FlowSemantics()

    listing = "\n".join(f"{i + 1}. {d}" for i, d in enumerate(usable))
    try:
        raw = ask_text(_FLOW_PROMPT.format(steps=listing)) or ""
    except Exception as exc:  # noqa: BLE001
        print(f"[semantics] flow labelling failed: {exc}", flush=True)
        return FlowSemantics()

    data = _parse_json_object(raw)
    if data is None:
        return FlowSemantics()

    tags = data.get("tags")
    return FlowSemantics(
        purpose=str(data.get("purpose") or "").strip(),
        tags=tuple(
            str(t).strip().lower() for t in tags if str(t).strip()
        )[:8]
        if isinstance(tags, list)
        else (),
        auto_name=str(data.get("name") or "").strip(),
    )


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """First JSON object in a model reply, or None.

    Same scrape-the-braces approach as `reason._parse` and
    `runner.draft_narration`: these models wrap JSON in prose often enough that
    strict parsing loses usable answers.
    """
    import json

    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
