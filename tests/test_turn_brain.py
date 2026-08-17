"""Gemini turn-brain parse + decide (mocked)."""

from __future__ import annotations

import json

import pytest

from navigator.agent.turn_brain import TurnDecision, decide_turn, parse_turn_decision


def test_parse_navigate():
    d = parse_turn_decision(
        '{"intent":"navigate_page","page_id":"inbox","nav_label":null,'
        '"spoken_response":"Opening inbox.","clean_intake":null}',
        allowed_pages={"dashboard", "inbox", "contacts"},
    )
    assert d.intent == "navigate_page"
    assert d.page_id == "inbox"


def test_parse_rejects_unknown_page():
    with pytest.raises(ValueError, match="page_id"):
        parse_turn_decision(
            '{"intent":"navigate_page","page_id":"nope","nav_label":null,'
            '"spoken_response":"x","clean_intake":null}',
            allowed_pages={"inbox"},
        )


def test_decide_turn_uses_vision():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    calls: dict = {}

    def fake(system: str, user: str, png_bytes: bytes) -> str:
        calls["ok"] = True
        assert b"PNG" in png_bytes[:8] or png_bytes.startswith(b"\x89PNG")
        return json.dumps(
            {
                "intent": "speak",
                "page_id": None,
                "nav_label": None,
                "spoken_response": "This is the dashboard overview.",
                "clean_intake": None,
            }
        )

    d = decide_turn(
        utterance="what is this?",
        screenshot_png=png,
        screen_text="url=https://resiliohub.com/dashboard/\ntitle=Dashboard",
        allowed_pages={"dashboard", "inbox"},
        product_brief="ResilioHub WhatsApp CRM",
        intake_summary="Devashish at ResilienceSoft",
        complete_with_image=fake,
    )
    assert isinstance(d, TurnDecision)
    assert d.intent == "speak"
    assert calls["ok"] is True


def test_should_track_screenshot_only_when_off_script_or_stuck():
    from navigator.agent.turn_brain import should_track_screenshot

    line = "Here is the send campaign button"
    assert should_track_screenshot(utterance="", expected_line=line) is False
    assert should_track_screenshot(
        utterance="click send campaign", expected_line=line
    ) is False
    assert should_track_screenshot(
        utterance="what about pricing", expected_line=line
    ) is True
    assert should_track_screenshot(
        utterance="ok", expected_line=line, stuck=True
    ) is True
