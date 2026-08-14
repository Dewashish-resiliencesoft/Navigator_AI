"""Gemini Vision turn brain — decide speak / nav / end each listen turn."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Literal

from navigator.agent.speech_persona import speech_rules
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

Intent = Literal["navigate_page", "click_nav", "speak", "end", "clarify"]

CompleteWithImage = Callable[[str, str, bytes], str]


class TurnDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent: Intent
    spoken_response: str
    page_id: str | None = None
    nav_label: str | None = None
    clean_intake: dict[str, str] | None = None

    @field_validator("spoken_response")
    @classmethod
    def _nonempty_spoken(cls, v: str) -> str:
        t = (v or "").strip()
        if not t:
            raise ValueError("spoken_response required")
        return t


def parse_turn_decision(raw: str, *, allowed_pages: set[str]) -> TurnDecision:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"turn brain non-JSON: {raw!r}") from e
    try:
        d = TurnDecision.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"invalid TurnDecision: {raw!r}") from e
    if d.intent == "navigate_page":
        if not d.page_id or d.page_id not in allowed_pages:
            raise ValueError(f"bad page_id {d.page_id!r} not in {sorted(allowed_pages)}")
    if d.intent == "click_nav" and not (d.nav_label or "").strip():
        raise ValueError("click_nav needs nav_label")
    return d


_SYSTEM = """You are a product specialist on a live screen-share demo for the
client's product (identity and facts come from the product brief / persona below — never invent a brand).
See the screenshot. Answer in JSON only (no markdown) with this schema:
{"intent":"navigate_page"|"click_nav"|"speak"|"end"|"clarify",
 "page_id":"<id or null>","nav_label":"<sidebar label or null>",
 "spoken_response":"1-2 short spoken sentences",
 "clean_intake":null}
Rules:
- Prefer page_id from the allowed list when user asks to go somewhere known.
- If the page is not in the list but a sidebar label is visible, use click_nav.
- If screenshot shows 404 / Not Found: apologize briefly and navigate_page to a safe home page_id (if allowed) or speak recovery — never pretend success.
- End phrases (goodbye, end the meeting, stop the demo) → intent end.
- Navigation ("take me to X", "show me Y") is NOT a correction — navigate or click_nav.
- Speak naturally; use the prospect's cleaned name if given; short sentences for voice.
- Stay consistent with the product brief; do not name unrelated products.
"""


def decide_turn(
    *,
    utterance: str,
    screenshot_png: bytes,
    screen_text: str,
    allowed_pages: set[str],
    product_brief: str = "",
    intake_summary: str = "",
    nav_labels: Sequence[str] | None = None,
    spoken_language: str = "en",
    agent_gender: str = "female",
    complete_with_image: CompleteWithImage | None = None,
) -> TurnDecision:
    if complete_with_image is None:
        from navigator.agent.providers import get_provider

        complete_with_image = get_provider().complete_with_image

    labels = list(nav_labels or [])
    lang_rule = speech_rules(
        spoken_language=spoken_language, agent_gender=agent_gender
    )
    user = "\n".join(
        [
            f"Utterance: {utterance}",
            f"Language: {lang_rule}",
            f"Allowed page_ids: {', '.join(sorted(allowed_pages))}",
            f"Known nav labels: {', '.join(labels) if labels else '(none)'}",
            f"Intake: {intake_summary or '(none)'}",
            "Product brief (trim):",
            (product_brief or "")[:2500],
            "Screen text:",
            (screen_text or "")[:1500],
            "Return JSON TurnDecision only.",
        ]
    )
    raw = complete_with_image(_SYSTEM, user, screenshot_png)
    return parse_turn_decision(raw, allowed_pages=allowed_pages)


def capture_screenshot_png(page) -> bytes:
    return page.screenshot(type="png", full_page=False)


_STOPWORDS = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "and",
    "or",
    "is",
    "it",
    "this",
    "that",
    "here",
    "let",
    "me",
    "you",
    "we",
    "on",
    "in",
    "for",
    "with",
    "looks",
    "good",
    "ok",
    "okay",
    "yes",
    "yeah",
}


def _content_words(text: str) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(w) > 2 and w not in _STOPWORDS
    }


def narration_aligned(utterance: str, expected_line: str) -> bool:
    """True when the client is talking about the current spoken line."""
    u = _content_words(utterance)
    e = _content_words(expected_line)
    if not u:
        return True
    if not e:
        return False
    return bool(u & e)


def should_track_screenshot(
    *,
    utterance: str = "",
    expected_line: str = "",
    stuck: bool = False,
) -> bool:
    """One PNG for the AI — only when stuck or the client left the current line.

    Meet/Zoom screenshare stays a live video. This is not that pipe.
    """
    if stuck:
        return True
    if not (utterance or "").strip():
        return False
    return not narration_aligned(utterance, expected_line)


def expected_narration_line(deps: object, state: dict | None = None) -> str:
    last = getattr(deps, "speaker", None)
    spoken = getattr(last, "last_spoken", None)
    if isinstance(spoken, str) and spoken.strip():
        return spoken
    said = getattr(last, "said", None)
    if isinstance(said, list) and said:
        return str(said[-1])
    if state:
        narr = state.get("narration") or []
        if narr:
            return str(narr[-1])
    return ""

