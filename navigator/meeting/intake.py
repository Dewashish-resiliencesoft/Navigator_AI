"""Pre-demo intake: greet, qualify the prospect, then pitch the wrapped product.

Answers: Meet STT listen callback, else stdin when interactive, else defaults
for CI. Questions spoken via TTS only — no Meet chat.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from navigator.meeting.intake_clean import (
    clean_business,
    clean_company,
    clean_name,
    clean_phrase,
    summarize_need,
)
from navigator.core.schemas import Persona
from navigator.voice.tts import Speaker


class ProspectIntake(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = ""
    company: str = ""
    business_type: str = ""
    looking_for: str = ""


_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    ("name", "What is your name?", "friend"),
    ("company", "Which company are you with?", "your company"),
    (
        "business_type",
        "What kind of business are you in?",
        "your industry",
    ),
    (
        "looking_for",
        "What are you looking for today — which workflow or problem should we focus on?",
        "seeing how the product works",
    ),
)


def greet_line(persona: Persona, prospect_name: str = "") -> str:
    who = prospect_name.strip() or "there"
    return (
        f"Hi {who}, I'm {persona.agent_name}. Thanks for joining — "
        f"I'll show you {persona.product_name} in a moment. "
        f"First I'd love to learn a bit about you so I can tailor the walkthrough."
    )


def name_ack_line(name: str) -> str:
    who = (name or "").strip() or "there"
    return f"Nice to meet you, {who}."


def solution_blurb(persona: Persona, looking_for: str) -> str:
    """Map prospect need → product angle (short, spoken)."""
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


def pitch_line(
    persona: Persona,
    intake: ProspectIntake,
    *,
    will_share_screen: bool = True,
) -> str:
    name = intake.name or "there"
    company = intake.company or "your team"
    biz = intake.business_type or "your business"
    need = summarize_need(intake.looking_for) or "what matters most to you"
    solve = solution_blurb(persona, intake.looking_for)
    closer = (
        "I'll share my screen and show you that live — jump in anytime."
        if will_share_screen
        else "I'll walk you through it live by voice — jump in anytime."
    )
    return (
        f"Got it, {name}. You're with {company} in {biz}, focused on {need}. "
        f"{solve} {closer}"
    )


def preferred_flow_id(looking_for: str) -> str | None:
    """Suggest an interrupt/demo flow from intake need."""
    need = (looking_for or "").lower()
    if any(
        k in need
        for k in ("automat", "flow", "bot", "qualify", "chatbot", "scale")
    ):
        return "show_automations"
    if any(k in need for k in ("inbox", "chat", "message", "reply", "conversation")):
        return "show_inbox"
    if any(k in need for k in ("analytic", "report", "metric", "convert", "funnel")):
        return "show_analytics"
    if any(k in need for k in ("contact", "lead", "crm", "customer", "pipeline")):
        return "show_contacts"
    return None


def format_with_intake(template: str, intake: ProspectIntake | None) -> str:
    """Fill {name}/{company}/{business}/{looking_for}/{need} in spoken lines."""
    if not template:
        return template
    need = (
        summarize_need(intake.looking_for)
        if intake and intake.looking_for
        else "what you care about"
    )
    if intake is None:
        return (
            template.replace("{name}", "there")
            .replace("{company}", "your team")
            .replace("{business}", "your business")
            .replace("{looking_for}", "what you care about")
            .replace("{need}", "what you care about")
        )
    return (
        template.replace("{name}", intake.name or "there")
        .replace("{company}", intake.company or "your team")
        .replace("{business}", intake.business_type or "your business")
        .replace("{looking_for}", need or "what you care about")
        .replace("{need}", need or "what you care about")
    )


def run_intake(
    *,
    persona: Persona,
    speaker: Speaker,
    interactive: bool,
    listen: Callable[[str], str] | None = None,
    prefill: dict[str, str] | None = None,
    will_share_screen: bool = True,
) -> ProspectIntake:
    """Ask intake questions via TTS; collect answers (STT / stdin / defaults).

    `prefill` is what the landing page already collected. A field that arrives
    filled is not asked again -- nobody wants to retype their company name to a
    bot thirty seconds after typing it into a form.
    """
    answers: dict[str, str] = {}
    prefill = {k: v.strip() for k, v in (prefill or {}).items() if v and v.strip()}

    hello = greet_line(persona, prefill.get("name", ""))
    _say(speaker, hello)

    for key, question, default in _QUESTIONS:
        if key in prefill:
            cleaned = _clean_field(key, prefill[key])
            answers[key] = cleaned or default
            print(f"[intake] {key}={answers[key]!r} (prefilled)", flush=True)
            if key == "name":
                _say(speaker, name_ack_line(answers["name"]))
            continue
        _say(speaker, question)
        if listen is not None:
            print(f"[intake] listening for {key}…", flush=True)
            heard = ""
            try:
                heard = (listen(question) or "").strip()
            except Exception as exc:  # noqa: BLE001
                print(f"[intake] listen failed ({exc}) — using default", flush=True)
            cleaned = extract_intake_entity(key, question, heard) if heard else ""
            answers[key] = cleaned or default
            print(
                f"[intake] {key}={answers[key]!r}"
                + ("" if heard else " (default)"),
                flush=True,
            )
        elif interactive:
            try:
                typed = input(f"[intake {key}] > ").strip()
            except EOFError:
                typed = ""
            cleaned = _clean_field(key, typed) if typed else ""
            answers[key] = cleaned or default
        else:
            answers[key] = _clean_field(key, default) or default
            print(f"[intake] (non-interactive) {key}={answers[key]!r}", flush=True)

        # Short human ack right after name — hybrid C backchannel.
        if key == "name":
            _say(speaker, name_ack_line(answers["name"]))

    intake = ProspectIntake(
        name=answers["name"],
        company=answers["company"],
        business_type=answers["business_type"],
        looking_for=answers["looking_for"],
    )
    pitch = pitch_line(persona, intake, will_share_screen=will_share_screen)
    _say(speaker, pitch)
    return intake


def _clean_field(key: str, value: str) -> str:
    if key == "name":
        return clean_name(value)
    if key == "company":
        return clean_company(value)
    if key == "business_type":
        return clean_business(value)
    if key == "looking_for":
        return summarize_need(value, max_len=120) or clean_phrase(value)
    return clean_phrase(value)


def extract_intake_entity(key: str, question: str, heard: str) -> str:
    from navigator.agent.providers import get_provider
    try:
        provider = get_provider()
    except RuntimeError as e:
        print(f"[intake] LLM fallback due to: {e}")
        return _clean_field(key, heard)

    sys_prompt = f"""You are an extraction assistant for a sales call.
The agent asked: "{question}"
The user replied: "{heard}"

Your task is to extract ONLY the specific piece of information requested (e.g., just the company name, just the person's name, or just the business type).
If the user says they don't have one, or gives a negative response, output: "NONE"
If the user's answer is ambiguous or doesn't contain the requested information, output: "NONE"
Do not output full sentences. Only output the extracted entity. Do not use quotes."""

    try:
        result = provider.complete(system=sys_prompt, user="Extract the entity.")
        if not result or result.strip().upper() == "NONE":
            return ""
        return result.strip()
    except Exception as exc:
        print(f"[intake] LLM extraction failed: {exc}", flush=True)
        return _clean_field(key, heard)


def _say(speaker: Speaker, text: str) -> None:
    print(f"[agent] {text}", flush=True)
    try:
        speaker.say(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] TTS skipped: {exc}", flush=True)
