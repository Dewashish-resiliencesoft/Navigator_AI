"""Pre-demo intake: greet, qualify the prospect, then pitch the wrapped product.

Answers come from Meet chat later (STT); for now interactive stdin or defaults
so CI stays non-interactive. Chat messages are sent into the meeting so the
prospect sees the questions even before screen share starts.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from navigator.meeting.attendee import AttendeeClient
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


def pitch_line(persona: Persona, intake: ProspectIntake) -> str:
    name = intake.name or "there"
    company = intake.company or "your team"
    biz = intake.business_type or "your business"
    need = intake.looking_for or "what matters most to you"
    positioning = persona.one_liner or persona.product_name
    return (
        f"Great, {name}. So you're at {company} in {biz}, and you're looking at "
        f"{need}. {persona.product_name} is {positioning}. "
        f"I'll share my screen now and walk you through it live — feel free to "
        f"jump in with your own data anytime."
    )


def run_intake(
    *,
    client: AttendeeClient,
    bot_id: str,
    persona: Persona,
    speaker: Speaker,
    interactive: bool,
) -> ProspectIntake:
    """Ask intake questions in Meet chat (+ local TTS); collect answers."""
    answers: dict[str, str] = {}

    # Initial greet (name still unknown).
    hello = greet_line(persona)
    _say_and_chat(client, bot_id, speaker, hello)

    for key, question, default in _QUESTIONS:
        _say_and_chat(client, bot_id, speaker, question)
        if interactive:
            try:
                typed = input(f"[intake {key}] > ").strip()
            except EOFError:
                typed = ""
            answers[key] = typed or default
        else:
            answers[key] = default
            print(f"[intake] (non-interactive) {key}={default!r}", flush=True)

    intake = ProspectIntake(
        name=answers["name"],
        company=answers["company"],
        business_type=answers["business_type"],
        looking_for=answers["looking_for"],
    )
    pitch = pitch_line(persona, intake)
    _say_and_chat(client, bot_id, speaker, pitch)
    return intake


def _say_and_chat(
    client: AttendeeClient, bot_id: str, speaker: Speaker, text: str
) -> None:
    print(f"[agent] {text}", flush=True)
    try:
        speaker.say(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] TTS skipped: {exc}", flush=True)
    try:
        client.send_chat(bot_id, text)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] Meet chat skipped: {exc}", flush=True)
