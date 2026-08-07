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
from navigator.automation.external_links import element_is_external

MODEL = "llama-3.3-70b-versatile"

_PROMPT = """You are exploring a product's web UI to build a guided demo.

Goal: map the product surface, then leave a CLEAN sales walkthrough — primary
nav, tabs, and feature pages a prospect would see. Prefer navigation that opens
a NEW page or view. Avoid bouncing back to pages already visited, logo/home
loops, settings trivia, logout, and anything that mutates data.

Current page: {url}
Already visited (prefer NEW destinations): {visited}
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


def _path_slugs(visited_paths: Sequence[str]) -> set[str]:
    slugs: set[str] = set()
    for path in visited_paths:
        parts = [p for p in str(path).strip("/").lower().split("/") if p]
        slugs.update(parts)
        if parts:
            slugs.add(parts[-1])
    return slugs


def _el_blob(el: dict[str, Any]) -> str:
    parts = [
        str(el.get(k) or "")
        for k in ("text", "label", "aria_label", "title", "name", "testid", "href")
    ]
    return " ".join(p for p in parts if p).lower()


def targets_visited_path(el: dict[str, Any], visited_paths: Sequence[str]) -> bool:
    """True when the control clearly points at a path we already mapped."""
    slugs = _path_slugs(visited_paths)
    if not slugs:
        return False
    blob = _el_blob(el)
    href = str(el.get("href") or "").lower()
    for slug in slugs:
        if len(slug) < 2:
            continue
        if slug in blob.split() or f"/{slug}" in href or href.endswith(slug):
            return True
        # Word-boundary-ish: "inbox" in "Go to Inbox"
        if re.search(rf"\b{re.escape(slug)}\b", blob):
            return True
    return False


def looks_like_nav(el: dict[str, Any]) -> bool:
    tag = str(el.get("tag") or "").lower()
    role = str(el.get("role") or "").lower()
    href = str(el.get("href") or "").strip()
    if tag == "a" and href and href != "#":
        return True
    return role in {"link", "tab", "menuitem", "treeitem"}


def _matches_focus(el: dict[str, Any], hint: str) -> bool:
    needle = (hint or "").strip().lower()
    if not needle or len(needle) < 2:
        return False
    blob = _el_blob(el)
    return needle in blob or re.search(rf"\b{re.escape(needle)}\b", blob)


def heuristic_pick(
    elements: Sequence[dict[str, Any]],
    visited_paths: Sequence[str],
    *,
    known_bad: dict[str, int] | None = None,
    product_base: str = "",
    page_url: str = "",
    focus_hint: str = "",
    skip: Callable[[dict[str, Any]], bool] | None = None,
) -> Choice | None:
    """Pick clear unvisited navigation without an LLM call.

    Keys that already failed unrepaired twice+ sink below everything else, so
    the explorer burns budget on fresh surface first. They are still eligible
    when they are the only option left.
    """
    ranked: list[tuple[int, dict[str, Any]]] = []
    for i, el in enumerate(elements):
        if el.get("fillable"):
            continue
        if product_base and element_is_external(el, product_base, page_url=page_url):
            continue
        if targets_visited_path(el, visited_paths):
            continue
        if skip is not None and skip(el):
            continue
        ranked.append((i, el))
    if not ranked:
        return None
    nav = [(i, e) for i, e in ranked if looks_like_nav(e)]
    pool = nav or ranked
    if focus_hint.strip():
        focused = [(i, e) for i, e in pool if _matches_focus(e, focus_hint)]
        if focused:
            pool = focused
    if known_bad:
        fresh = [(i, e) for i, e in pool if known_bad.get(element_key(e), 0) < 2]
        if fresh:
            pool = fresh
    idx, _el = pool[0]
    return Choice(idx, "heuristic: unvisited destination", "")


def choose_next(
    *,
    url: str,
    elements: Sequence[dict[str, Any]],
    corrections: Sequence[str] = (),
    visited_paths: Sequence[str] = (),
    known_bad: dict[str, int] | None = None,
    product_base: str = "",
    focus_hint: str = "",
    skip: Callable[[dict[str, Any]], bool] | None = None,
    ask_text: Callable[[str], str] | None = None,
    ask_vision: Callable[[str, str], str] | None = None,
    screenshot: str = "",
) -> Choice | None:
    """Pick one element. None means "no usable choice" -> caller stops/backtracks."""
    if not elements:
        return None

    # Prefer unlabeled-free nav heuristics — avoids Groq TPD + multi-second waits.
    if not needs_vision(elements):
        fast = heuristic_pick(
            elements,
            visited_paths,
            known_bad=known_bad,
            product_base=product_base,
            page_url=url,
            focus_hint=focus_hint,
            skip=skip,
        )
        if fast is not None:
            return fast

    corr_block = ""
    if corrections:
        corr_block = (
            "Known issues from previous runs (do not repeat these):\n"
            + "\n".join(f"- {c}" for c in corrections)
            + "\n"
        )
    focus_block = ""
    hint = (focus_hint or "").strip()
    if hint:
        focus_block = (
            f"Client asked to prioritize this area first: {hint!r}. "
            "Prefer nav/tabs whose label matches before unrelated pages.\n"
        )
    visited = ", ".join(visited_paths) if visited_paths else "(none yet)"
    # Drop obvious backtrack targets from the LLM menu when alternatives exist.
    filtered = [
        (i, e)
        for i, e in enumerate(elements)
        if not targets_visited_path(e, visited_paths)
        and not (
            product_base and element_is_external(e, product_base, page_url=url)
        )
        and not (skip is not None and skip(e))
    ]
    # Deprioritize twice-failed keys in the LLM menu too, without removing them
    # entirely — an empty menu would force a stall when only known-bad remains.
    if known_bad:
        fresh = [(i, e) for i, e in filtered if known_bad.get(element_key(e), 0) < 2]
        if fresh:
            filtered = fresh
    menu = filtered if filtered else list(enumerate(elements))
    index_map = [i for i, _ in menu]
    menu_els = [e for _, e in menu]

    prompt = _PROMPT.format(
        url=url,
        visited=visited,
        corrections=corr_block + focus_block,
        elements=format_elements(menu_els),
    )

    raw = ""
    if needs_vision(menu_els) and ask_vision is not None and screenshot:
        try:
            raw = ask_vision(prompt, screenshot)
        except Exception as exc:  # noqa: BLE001
            print(f"[explore] vision reason failed: {exc}", flush=True)
            raw = ""
    if not raw and ask_text is not None:
        try:
            raw = ask_text(prompt)
        except Exception as exc:  # noqa: BLE001
            if "stopped by client" in str(exc).lower():
                return None
            print(f"[explore] text reason failed: {exc}", flush=True)
            raw = ""

    choice = _parse(raw, len(menu_els))
    if choice is not None:
        return Choice(index_map[choice.index], choice.why, choice.narration)
    # Fallback: first non-visited target, else first untried.
    fast = heuristic_pick(
        elements,
        visited_paths,
        known_bad=known_bad,
        product_base=product_base,
        page_url=url,
        skip=skip,
    )
    if fast is not None:
        return fast
    return Choice(index_map[0], "fallback: first untried element", "")


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
