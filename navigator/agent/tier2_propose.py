"""Tier 2 live proposer: perceive → reason → one click proposal.

Reuses the offline explorer's inventory + Choice shape. Live path is stricter:
fill fields are never proposed (read-only default), and the guardrail in
`run_tier2` still decides whether the click may run.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from navigator.agent.tier2 import Tier2Proposal
from navigator.automation.explore import perceive
from navigator.automation.explore.reason import Choice, format_elements, needs_vision
from navigator.automation.record import junk_record_reason, prefer_selector
from navigator.core.schemas import ClickElement, Postcondition
from navigator.core.settings import settings
from navigator.knowledge.site_graph import SiteGraph

def _tier2_model() -> str:
    from navigator.core.settings import settings

    return settings.brain_planning_model

_PROMPT = """You are on a LIVE product demo call. The prospect just asked:
{utterance}

Pick ONE on-screen control that is safe to click to help answer or show that.
Prefer navigation, tabs, views, expanders, filters — read-only discovery.
Never pick anything that sends, submits, deletes, pays, invites, publishes,
logs out, or changes real data.

Current page: {url}

Clickable elements (choose one by index):
{elements}

Reply with JSON only:
{{"index": <int>, "why": "<short>", "narration": "<one spoken sentence>"}}"""


def clickable_inventory(page) -> list[dict[str, Any]]:
    """Visible click targets only — no fills on the live Tier-2 path."""
    out: list[dict[str, Any]] = []
    for el in perceive.inventory(page):
        if perceive.is_fillable(el):
            continue
        alias, css = prefer_selector(el)
        if junk_record_reason(el, alias=alias, selector=css):
            continue
        out.append(el)
    return out


def choose_for_utterance(
    *,
    utterance: str,
    url: str,
    elements: Sequence[dict[str, Any]],
    ask_text: Callable[[str], str] | None = None,
    ask_vision: Callable[[str, str], str] | None = None,
    screenshot: str = "",
) -> Choice | None:
    if not elements:
        return None
    prompt = _PROMPT.format(
        utterance=utterance.strip() or "(no question)",
        url=url,
        elements=format_elements(elements),
    )
    raw = ""
    if needs_vision(elements) and ask_vision is not None and screenshot:
        try:
            raw = ask_vision(prompt, screenshot)
        except Exception as exc:  # noqa: BLE001
            print(f"[tier2] vision reason failed: {exc}", flush=True)
    if not raw and ask_text is not None:
        try:
            raw = ask_text(prompt)
        except Exception as exc:  # noqa: BLE001
            print(f"[tier2] text reason failed: {exc}", flush=True)
    return _parse(raw, len(elements))


def propose_from_page(
    *,
    utterance: str,
    page,
    ask_text: Callable[[str], str] | None = None,
    ask_vision: Callable[[str, str], str] | None = None,
) -> Tier2Proposal | None:
    """Perceive current page and propose one click grounded in the inventory."""
    if page is None:
        return None
    elements = clickable_inventory(page)
    if not elements:
        return None

    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""

    shot = ""
    if needs_vision(elements):
        shot = perceive.screenshot_b64(page)

    text = ask_text
    if text is None:
        text = _default_ask_text()

    choice = choose_for_utterance(
        utterance=utterance,
        url=url,
        elements=elements,
        ask_text=text,
        ask_vision=ask_vision,
        screenshot=shot,
    )
    if choice is None:
        return None

    el = elements[choice.index]
    alias, css = prefer_selector(el)
    # Verify against body — alias is ephemeral and may not stay after click.
    call = ClickElement(
        selector=alias,
        expects=Postcondition(check="visible", selector="body"),
    )
    spoken = choice.narration or f"Let me open {alias.replace('_', ' ')}."
    # Stash css on the element so the planner can bind it into the site graph.
    el_out = {**el, "_tier2_alias": alias, "_tier2_css": css}
    return Tier2Proposal(element=el_out, call=call, spoken=spoken)


def bind_ephemeral_selector(
    graph: SiteGraph, page_id: str, alias: str, css: str
) -> SiteGraph:
    """Add alias→css on this page for one Tier-2 click. Does not publish."""
    page = graph.page(page_id)
    if page.selectors.get(alias) == css:
        return graph
    selectors = {**page.selectors, alias: css}
    # body must exist for the postcondition above
    selectors.setdefault("body", "body")
    new_page = page.model_copy(update={"selectors": selectors})
    return graph.model_copy(update={"pages": {**graph.pages, page_id: new_page}})


def _default_ask_text() -> Callable[[str], str] | None:
    key = (settings.groq_api_key or "").strip()
    if not key:
        return None

    def ask(prompt: str) -> str:
        from groq import Groq

        resp = Groq(api_key=key).chat.completions.create(
            model=_tier2_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=200,
        )
        return resp.choices[0].message.content or ""

    return ask


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
