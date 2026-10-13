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



_HINGLISH = {
    "bol", "rahi", "raha", "rahe", "ho", "hai", "hain", "kya", "nahi", "na", "haan",
    "ji", "acha", "accha", "theek", "thik", "matlab", "yaar", "toh", "to", "ki", "ka",
    "ke", "ko", "se", "mein", "main", "aap", "tum", "like", "um", "uh", "hmm", "okay",
    "ok", "so", "the", "a", "an", "and", "is", "it", "i", "you", "me", "my", "just",
}


def clean_positioning(text: str, max_chars: int = 140) -> str:
    """One clean spoken sentence from a one-liner that may be a raw page dump."""
    import re

    t = text or ""
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"(?im)^\s*(url|title)\s*:.*$", " ", t)
    t = re.sub(r"[#*_`>|\[\]]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -:|")
    m = re.search(r"(.+?[.!?])(\s|$)", t)
    if m and len(m.group(1)) >= 20:
        t = m.group(1)
    if len(t) > max_chars:
        t = t[:max_chars].rsplit(" ", 1)[0]
    t = t.rstrip(" ,;:-.")
    words = t.split()
    # Nav-list dumps: mostly Capitalised single words, no verbs -> reject.
    caps = sum(1 for w in words if w[:1].isupper())
    if len(words) < 3 or (len(words) >= 5 and caps / len(words) > 0.7):
        return ""
    return t


def meaningful_need(text: str) -> str:
    """Return the need only if it reads like a real ask (not Hinglish/noise)."""
    import re

    t = (summarize_need(text or "") or text or "").strip()
    toks = re.findall(r"[A-Za-z]+", t.lower())
    if len(t) < 4 or not toks:
        return ""
    filler = sum(1 for w in toks if w in _HINGLISH)
    content = [w for w in toks if w not in _HINGLISH and len(w) > 2]
    if not content or filler / len(toks) >= 0.4:
        return ""
    return t

def solution_blurb(persona: Persona, looking_for: str) -> str:
    """Map prospect need → product angle (short, spoken). Tenant-neutral."""
    persona = prospect_facing_persona(persona)
    need = meaningful_need(looking_for)
    product = persona.product_name
    positioning = clean_positioning(persona.one_liner or "")
    if positioning and need:
        return (
            f"{product} is {positioning} — we'll focus the walkthrough on what "
            f"you asked about: {summarize_need(need) or need}."
        )
    if positioning:
        return f"{product} is {positioning} — I'll tailor the walkthrough to you."
    if need:
        short = summarize_need(need) or need
        return (
            f"I'll show how {product} helps with {short} — "
            f"we will walk the parts that matter for you."
        )
    return f"I'll walk you through {product} live, step by step."


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
    # Only use a name we already collected (form prefill). Unknown → short
    # self-intro; "What is your name?" follows — never "Hi there" + later ack.
    if lang == "hi":
        if who:
            return (
                f"नमस्ते {who}, मैं {persona.agent_name} हूँ। "
                f"जुड़ने के लिए धन्यवाद — अभी {persona.product_name} live दिखा"
                f"{'ता' if agent_gender == 'male' else 'ती'} हूँ।"
            )
        return (
            f"नमस्ते, मैं {persona.agent_name} हूँ। "
            f"आज {persona.product_name} live दिखा"
            f"{'ऊंगा' if agent_gender == 'male' else 'ऊँगी'}।"
        )
    if who:
        return (
            f"Hi {who}, I'm {persona.agent_name}. Thanks for joining — "
            f"I'll show you {persona.product_name} live in just a moment."
        )
    return (
        f"Hi, I'm {persona.agent_name}. Thanks for joining — "
        f"I'll show you {persona.product_name} live today."
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
