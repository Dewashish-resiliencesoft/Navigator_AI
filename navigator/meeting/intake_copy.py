"""Intake spoken lines — English and Hindi (from dashboard default_language)."""

from __future__ import annotations

from navigator.agent.speech_safety import prospect_facing_persona
from navigator.core.agent_settings import AgentGender, SpokenLanguage
from navigator.core.schemas import Persona
from navigator.meeting.intake_clean import summarize_need

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navigator.meeting.intake import ProspectIntake


def _product_name(persona: Persona) -> str:
    return prospect_facing_persona(persona).product_name


def solution_blurb(persona: Persona, looking_for: str) -> str:
    """Map prospect need → product angle (short, spoken)."""
    persona = prospect_facing_persona(persona)
    need = (looking_for or "").lower()
    product = persona.product_name
    if any(k in need for k in ("inbox", "chat", "message", "reply", "conversation")):
        return (
            f"{product} gives your team a shared WhatsApp inbox so nothing slips "
            f"between phones — exactly for that conversation problem."
        )
    if any(k in need for k in ("contact", "lead", "crm", "customer", "pipeline")):
        return (
            f"{product} keeps every WhatsApp lead and customer in one contacts "
            f"view so the team shares one source of truth."
        )
    if any(
        k in need
        for k in ("automat", "flow", "bot", "qualify", "24", "scale", "chatbot")
    ):
        return (
            f"{product} runs chat flows that greet, qualify, and route people "
            f"on WhatsApp without someone typing every reply."
        )
    if any(k in need for k in ("analytic", "report", "metric", "convert", "funnel")):
        return (
            f"{product} surfaces conversation analytics — volume, response time, "
            f"what converts — so you can see the funnel clearly."
        )
    positioning = persona.one_liner or "WhatsApp CRM and automation for sales teams"
    return f"{product} is {positioning} — we'll focus the walkthrough on what you asked for."


def demo_kickoff_line(*, lang: SpokenLanguage = "en") -> str:
    if lang == "hi":
        return "चलिए, बिना समय गँवाए डेमो शुरू करते हैं।"
    return "Without wasting any time, let's get started with the demo."


def greet_line(
    persona: Persona,
    prospect_name: str = "",
    *,
    lang: SpokenLanguage = "en",
    agent_gender: AgentGender = "female",
) -> str:
    from navigator.agent.speech_safety import prospect_facing_persona

    persona = prospect_facing_persona(persona)
    who = prospect_name.strip()
    if lang == "hi":
        if who:
            open_ = f"नमस्ते {who}, मैं {persona.agent_name} हूँ।"
        else:
            open_ = f"नमस्ते, मैं {persona.agent_name} हूँ।"
        tail_f = (
            f"जुड़ने के लिए धन्यवाद — अभी {persona.product_name} live दिखाती हूँ।"
        )
        tail_m = (
            f"जुड़ने के लिए धन्यवाद — अभी {persona.product_name} live दिखाता हूँ।"
        )
        return open_ + " " + (tail_m if agent_gender == "male" else tail_f)
    who_en = who or "there"
    return (
        f"Hi {who_en}, I'm {persona.agent_name}. Thanks for joining — "
        f"I'll show you {persona.product_name} live in just a moment."
    )


def name_ack_line(name: str, *, lang: SpokenLanguage = "en") -> str:
    who = (name or "").strip() or ("वहाँ" if lang == "hi" else "there")
    if lang == "hi":
        return f"आपसे मिलकर अच्छा लगा, {who}।"
    return f"Nice to meet you, {who}."


def intake_questions(
    *, lang: SpokenLanguage = "en", product_name: str = "the product"
) -> tuple[tuple[str, str, str], ...]:
    product = (product_name or "the product").strip() or "the product"
    if lang == "hi":
        return (
            ("name", "आपका नाम क्या है?", "friend"),
            (
                "looking_for",
                f"आज आप {product} से क्या achieve करना चाहते हैं?",
                "seeing how the product works",
            ),
        )
    return (
        ("name", "What is your name?", "friend"),
        (
            "looking_for",
            f"What would you like {product} to help you with today?",
            "seeing how the product works",
        ),
    )


def pitch_line(
    persona: Persona,
    intake: "ProspectIntake",
    *,
    lang: SpokenLanguage = "en",
    agent_gender: AgentGender = "female",
    will_share_screen: bool = True,
) -> str:
    name = intake.name or ("वहाँ" if lang == "hi" else "there")
    need = summarize_need(intake.looking_for) if intake.looking_for else ""
    solve = (
        solution_blurb(persona, intake.looking_for)
        if need
        else (
            f"{_product_name(persona)} — "
            + ("जैसे चलेगा वैसे देखते हैं।" if lang == "hi" else "we'll explore what fits as we go.")
        )
    )
    if lang == "hi":
        closer_f = "मैं screen share करके live दिखाती हूँ — बीच में कभी भी बोलिए।"
        closer_m = "मैं screen share करके live दिखाता हूँ — बीच में कभी भी बोलिए।"
        closer = closer_m if agent_gender == "male" else closer_f
        if not will_share_screen:
            closer = "मैं voice पर walkthrough जारी रखूँगी — बीच में पूछिए।"
        context = _intake_context_hi(intake, need=need)
        return f"समझ गई, {name}. {context} {solve} {closer}".replace(
            "समझ गई", "समझ गया" if agent_gender == "male" else "समझ गई"
        )
    closer = (
        "I'll share my screen and show you that live — jump in anytime."
        if will_share_screen
        else "I'll walk you through it live by voice — jump in anytime."
    )
    context = _intake_context_en(intake, need=need)
    return f"Got it, {name}. {context} {solve} {closer}"


def _intake_context_en(intake: ProspectIntake, *, need: str) -> str:
    bits: list[str] = []
    if intake.company and intake.business_type:
        bits.append(f"You're with {intake.company} in {intake.business_type}")
    elif intake.company:
        bits.append(f"You're with {intake.company}")
    elif intake.business_type:
        bits.append(f"You're in {intake.business_type}")
    if need:
        bits.append(f"focused on {need}")
    if not bits:
        return "Thanks for that."
    return ", ".join(bits) + "."


def _intake_context_hi(intake: ProspectIntake, *, need: str) -> str:
    bits: list[str] = []
    if intake.company and intake.business_type:
        bits.append(f"आप {intake.company} में {intake.business_type} में हैं")
    elif intake.company:
        bits.append(f"आप {intake.company} से हैं")
    elif intake.business_type:
        bits.append(f"आप {intake.business_type} में हैं")
    if need:
        bits.append(f"focus {need} पर है")
    if not bits:
        return "धन्यवाद।"
    return ", ".join(bits) + "।"
