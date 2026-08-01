"""Pre-demo intake: greet, qualify the prospect, then pitch the wrapped product.

Answers: Meet STT listen callback, else stdin when interactive, else defaults
for CI. Questions spoken via TTS only — no Meet chat.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from navigator.meeting.intake_clean import clean_name, clean_phrase
from navigator.schemas import Persona
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


def pitch_line(persona: Persona, intake: ProspectIntake) -> str:
    name = intake.name or "there"
    company = intake.company or "your team"
    biz = intake.business_type or "your business"
    need = intake.looking_for or "what matters most to you"
    solve = solution_blurb(persona, intake.looking_for)
    return (
        f"Got it, {name}. You're at {company} in {biz}, looking at {need}. "
        f"{solve} "
        f"I'll share my screen and show you that live — jump in anytime."
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
        .replace("{looking_for}", intake.looking_for or "what you care about")
        .replace("{need}", intake.looking_for or "what you care about")
    )


def run_intake(
    *,
    persona: Persona,
    speaker: Speaker,
    interactive: bool,
    listen: Callable[[str], str] | None = None,
    prefill: dict[str, str] | None = None,
) -> ProspectIntake:
    """Ask intake questions via TTS; collect answers (STT / stdin / defaults).

    `prefill` is what the landing page already collected. A field that arrives
    filled is not asked again -- nobody wants to retype their company name to a
    bot thirty seconds after typing it into a form.
    """
    answers: dict[str, str] = {}
    prefill = {k: v.strip() for k, v in (prefill or {}).items() if v and v.strip()}

    # Initial greet (name still unknown).
    hello = greet_line(persona)
    _say(speaker, hello)

    for key, question, default in _QUESTIONS:
        if key in prefill:
            answers[key] = _clean_field(key, prefill[key])
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
            answers[key] = heard or default
            answers[key] = _clean_field(key, answers[key])
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
            answers[key] = _clean_field(key, typed or default)
        else:
            answers[key] = _clean_field(key, default)
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
    pitch = pitch_line(persona, intake)
    _say(speaker, pitch)
    return intake


def _clean_field(key: str, value: str) -> str:
    if key == "name":
        return clean_name(value) or value
    return clean_phrase(value) or value


def _say(speaker: Speaker, text: str) -> None:
    print(f"[agent] {text}", flush=True)
    try:
        speaker.say(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] TTS skipped: {exc}", flush=True)
