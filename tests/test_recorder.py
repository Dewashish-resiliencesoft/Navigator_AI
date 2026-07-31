"""Recorder draft site graph helpers."""

from __future__ import annotations

from navigator.record import (
    RecordedStep,
    draft_site_graph,
    guess_postcondition,
    prefer_selector,
)


def test_prefer_selector_testid():
    alias, css = prefer_selector(
        {"testid": "send-btn", "id": "x", "tag": "button", "text": "Send"}
    )
    assert alias == "send_btn"
    assert css == '[data-testid="send-btn"]'


def test_prefer_selector_id_fallback():
    alias, css = prefer_selector({"testid": "", "id": "composer", "tag": "input"})
    assert css == "#composer"
    assert alias == "composer"


def test_draft_site_graph_builds_flow():
    steps = [
        RecordedStep(
            tool="fill_field",
            alias="message_input",
            selector="#msg",
            value="hi",
            postcondition=guess_postcondition(
                RecordedStep("fill_field", "message_input", "#msg", "hi")
            ),
        ),
        RecordedStep(
            tool="click_element",
            alias="send_button",
            selector="#send",
        ),
    ]
    steps[1].postcondition = guess_postcondition(steps[1])
    draft = draft_site_graph(
        base_url="https://example.com", product_name="Demo", steps=steps
    )
    assert draft["_meta"]["draft"] is True
    page = draft["pages"]["main"]
    assert "message_input" in page["elements"]
    assert page["flows"]["recorded_demo"][0]["tool"] == "fill_field"
    assert page["flows"]["recorded_demo"][1]["expects"]["check"] == "visible"
