"""Prospect intake copy."""

from __future__ import annotations

from navigator.meeting.intake import ProspectIntake, greet_line, pitch_line, run_intake
from navigator.core.schemas import Persona


class _RecordingSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)


def test_quick_greet_and_intake_from_prefill():
    from navigator.meeting.intake import (
        demo_kickoff_line,
        intake_from_prefill,
        quick_greet_line,
    )

    persona = Persona(product_name="Acme", agent_name="Nav", one_liner="x")
    greet = quick_greet_line(persona, "Sam")
    assert "Sam" in greet and "Nav" in greet
    assert "share" in greet.lower()
    assert "let's get started" in demo_kickoff_line().lower()
    intake = intake_from_prefill({"company": "Co"}, human_name="Sam")
    assert intake.name == "Sam"
    assert intake.company == "Co"


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


def test_pitch_paraphrases_noisy_stt_not_dumps_it():
    persona = Persona(product_name="ResilioHub", one_liner="x", agent_name="N")
    intake = ProspectIntake(
        name="Dewashish",
        company="ResilientSoft",
        business_type="product and service company",
        looking_for=(
            "Yeah, actually we need like we have a sharp quiz app, which is a "
            "quiz game. We need WhatsApp CRM support for that"
        ),
    )
    line = pitch_line(persona, intake)
    assert "Yeah, actually" not in line
    assert "I'm with" not in line
    assert "Dewashish" in line
    assert "ResilientSoft" in line


def test_pitch_skips_screenshare_claim_when_disabled():
    persona = Persona(product_name="P", one_liner="x", agent_name="N")
    intake = ProspectIntake(name="A", company="B", business_type="SaaS", looking_for="crm")
    line = pitch_line(persona, intake, will_share_screen=False)
    assert "share my screen" not in line.lower()
    assert "screen" not in line.lower() or "walk" in line.lower()

def test_run_intake_does_not_send_chat():
    speaker = _RecordingSpeaker()
    persona = Persona(product_name="P", agent_name="Nav")
    run_intake(
        persona=persona,
        speaker=speaker,
        interactive=False,
    )
    assert len(speaker.said) >= 6  # greet + 4 questions + pitch


def test_run_intake_uses_listen_answers():
    speaker = _RecordingSpeaker()
    persona = Persona(product_name="P", agent_name="Nav")
    answers = iter(["Dewa", "Resilio", "SaaS", "inbox"])

    def listen(_prompt: str) -> str:
        return next(answers)

    intake = run_intake(
        persona=persona,
        speaker=speaker,
        interactive=False,
        listen=listen,
    )
    assert intake.name == "Dewa"
    assert intake.company == "Resilio"
    assert intake.business_type == "SaaS"
    assert intake.looking_for == "inbox"
    # Name ack + personalized pitch
    assert any("Nice to meet you, Dewa" in s for s in speaker.said)
    assert any("Dewa" in s and "inbox" in s.lower() for s in speaker.said)


def test_prefilled_fields_are_not_asked_again():
    """The landing page already took their name; don't make them repeat it."""
    speaker = _RecordingSpeaker()
    persona = Persona(product_name="P", agent_name="Nav")
    asked: list[str] = []

    def listen(prompt: str) -> str:
        asked.append(prompt)
        return "spoken"

    intake = run_intake(
        persona=persona,
        speaker=speaker,
        interactive=False,
        listen=listen,
        prefill={"name": "Dewa", "company": "Resilio", "looking_for": ""},
    )
    assert intake.name == "Dewa"
    assert intake.company == "Resilio"
    assert intake.business_type == "spoken", "an empty prefill still gets asked"
    assert intake.looking_for == "spoken"
    assert len(asked) == 2, f"only the unfilled questions: {asked}"
    assert any("Nice to meet you, Dewa" in s for s in speaker.said)


def test_no_prefill_asks_everything():
    speaker = _RecordingSpeaker()
    asked: list[str] = []
    run_intake(
        persona=Persona(product_name="P", agent_name="Nav"),
        speaker=speaker,
        interactive=False,
        listen=lambda p: asked.append(p) or "x",
    )
    assert len(asked) == 4


def test_preferred_flow_from_looking_for():
    from navigator.meeting.intake import preferred_flow_id, solution_blurb

    assert preferred_flow_id("shared inbox for chats") == "show_inbox"
    assert preferred_flow_id("automate lead qualification") == "show_automations"
    blurb = solution_blurb(
        Persona(product_name="ResilioHub", one_liner="x", agent_name="N"),
        "shared inbox",
    )
    assert "inbox" in blurb.lower()
