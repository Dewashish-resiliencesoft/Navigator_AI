"""Prospect intake copy."""

from __future__ import annotations

from navigator.meeting.intake import ProspectIntake, greet_line, pitch_line
from navigator.schemas import Persona


def test_greet_uses_persona_agent_and_product():
    persona = Persona(
        product_name="the WhatsApp CRM dashboard",
        one_liner="a shared inbox for sales teams",
        agent_name="Navigator",
    )
    line = greet_line(persona)
    assert "Navigator" in line
    assert "WhatsApp CRM" in line


def test_pitch_weaves_intake_and_persona():
    persona = Persona(
        product_name="Acme Inbox",
        one_liner="shared WhatsApp for support",
        agent_name="Ada",
    )
    intake = ProspectIntake(
        name="Priya",
        company="ResilienceSoft",
        business_type="B2B SaaS",
        looking_for="a shared inbox workflow",
    )
    line = pitch_line(persona, intake)
    assert "Priya" in line
    assert "ResilienceSoft" in line
    assert "B2B SaaS" in line
    assert "shared inbox" in line.lower() or "Acme Inbox" in line
    assert "share my screen" in line.lower() or "screen" in line.lower()
