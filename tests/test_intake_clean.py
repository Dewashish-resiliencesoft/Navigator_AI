"""Intake STT cleanup."""

from navigator.meeting.intake_clean import clean_name, clean_phrase


def test_clean_name_strips_my_name_is():
    assert clean_name("My name is Devashish.") == "Devashish"


def test_clean_name_strips_hello():
    assert clean_name("hello my name is Dewashish") == "Dewashish"


def test_clean_phrase_keeps_short():
    out = clean_phrase(
        "I want my work to be automated using the WhatsApp CRM."
    )
    assert "WhatsApp" in out
