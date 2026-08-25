"""Intake STT cleanup."""

from navigator.meeting.intake_clean import (
    clean_business,
    clean_company,
    clean_name,
    clean_phrase,
    summarize_need,
)


def test_clean_name_strips_my_name_is():
    assert clean_name("My name is Devashish.") == "Devashish"


def test_clean_name_strips_hello():
    assert clean_name("hello my name is Dewashish") == "Dewashish"


def test_clean_name_rejects_filler_yeah():
    assert clean_name("Yeah") == ""
    assert clean_name("yes") == ""


def test_clean_name_rejects_agent_and_platform_names():
    """STT often echoes the bot greeting ('Hi, I'm Navigator AI')."""
    assert clean_name("Navigator AI") == ""
    assert clean_name("navigator") == ""
    assert clean_name("I'm Navigator AI") == ""
    assert clean_name("Hi there I'm Navigator") == ""
    assert clean_name("Resiliohub Agent", reserved=frozenset({"resiliohub agent"})) == ""
    assert clean_name("Dewashish", reserved=frozenset({"navigator ai"})) == "Dewashish"


def test_clean_phrase_keeps_short():
    out = clean_phrase(
        "I want my work to be automated using the WhatsApp CRM."
    )
    assert "WhatsApp" in out


def test_clean_company_strips_im_with():
    assert clean_company("I'm with ResilientSoft") == "ResilientSoft"
    assert clean_company("we work at Acme Inc") == "Acme Inc"


def test_clean_business_strips_we_are_in():
    out = clean_business("We are in a product-based and service-based company")
    assert out.lower().startswith("product")
    assert "we are" not in out.lower()


def test_summarize_need_drops_filler_and_shortens():
    raw = (
        "Yeah, actually we need like we have a sharp quiz app, which is a quiz "
        "game. We need WhatsApp CRM support for that"
    )
    out = summarize_need(raw)
    assert "yeah" not in out.lower()
    assert "whatsapp" in out.lower() or "crm" in out.lower()
    assert len(out) <= 90
