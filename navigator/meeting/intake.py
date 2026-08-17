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
    is_declined,
    summarize_need,
)
from navigator.core.agent_settings import AgentGender, SpokenLanguage
from navigator.core.schemas import Persona
from navigator.voice.tts import Speaker


class ProspectIntake(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = ""
    company: str = ""
    business_type: str = ""
    looking_for: str = ""


def demo_kickoff_line(*, lang: SpokenLanguage = "en") -> str:
    from navigator.meeting.intake_copy import demo_kickoff_line as _line

    return _line(lang=lang)


def quick_greet_line(
    persona: Persona,
    prospect_name: str = "",
    *,
    lang: SpokenLanguage = "en",
    agent_gender: AgentGender = "female",
) -> str:
    """Short greet right after human joins — no intake Q&A."""
    from navigator.meeting.intake_copy import greet_line as _greet

    return _greet(
        persona,
        prospect_name,
        lang=lang,
        agent_gender=agent_gender,
    )


def greet_line(
    persona: Persona,
    prospect_name: str = "",
    *,
    lang: SpokenLanguage = "en",
    agent_gender: AgentGender = "female",
) -> str:
    from navigator.meeting.intake_copy import greet_line as _greet

    return _greet(
        persona,
        prospect_name,
        lang=lang,
        agent_gender=agent_gender,
    )


def name_ack_line(name: str, *, lang: SpokenLanguage = "en") -> str:
    from navigator.meeting.intake_copy import name_ack_line as _ack

    return _ack(name, lang=lang)


def intake_from_prefill(
    prefill: dict[str, str] | None = None,
    *,
    human_name: str = "",
) -> ProspectIntake:
    """Build intake from landing-page / signup data — no spoken questions."""
    raw = {k: v.strip() for k, v in (prefill or {}).items() if v and v.strip()}
    if human_name.strip() and "name" not in raw:
        raw["name"] = human_name.strip()
    return ProspectIntake(
        name=_clean_field("name", raw.get("name", "")) or "there",
        company=_clean_field("company", raw.get("company", "")) or "your team",
        business_type=_clean_field("business_type", raw.get("business_type", ""))
        or "your business",
        looking_for=_clean_field("looking_for", raw.get("looking_for", ""))
        or "seeing how the product works",
    )


def usable_meeting_display_name(name: str) -> str:
    """Meet/Zoom join label when it is a real person name — skip asking."""
    n = " ".join((name or "").split())
    if not n:
        return ""
    low = n.lower()
    if low in {
        "guest",
        "meet guest",
        "zoom user",
        "iphone",
        "android",
        "unknown",
        "user",
    }:
        return ""
    digits = "".join(c for c in n if c.isdigit())
    if len(digits) >= 8:
        return ""
    if "@" in n:
        return ""
    return n


def solution_blurb(persona: Persona, looking_for: str) -> str:
    from navigator.meeting.intake_copy import solution_blurb as _blurb

    return _blurb(persona, looking_for)


def pitch_line(
    persona: Persona,
    intake: ProspectIntake,
    *,
    lang: SpokenLanguage = "en",
    agent_gender: AgentGender = "female",
    will_share_screen: bool = True,
) -> str:
    from navigator.meeting.intake_copy import pitch_line as _pitch

    return _pitch(
        persona,
        intake,
        lang=lang,
        agent_gender=agent_gender,
        will_share_screen=will_share_screen,
    )


def prospect_facing_product(persona: Persona) -> str:
    from navigator.agent.speech_safety import prospect_facing_persona

    return prospect_facing_persona(persona).product_name


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
    spoken_language: SpokenLanguage = "en",
    agent_gender: AgentGender = "female",
    extra_languages: tuple[SpokenLanguage, ...] = ("hi",),
    fast_extract: bool = False,
) -> ProspectIntake:
    """Ask intake questions via TTS; collect answers (STT / stdin / defaults).

    `prefill` is what the landing page already collected. A field that arrives
    filled is not asked again -- nobody wants to retype their company name to a
    bot thirty seconds after typing it into a form.
    """
    answers: dict[str, str] = {}
    prefill = {k: v.strip() for k, v in (prefill or {}).items() if v and v.strip()}
    lang: SpokenLanguage = spoken_language
    product = prospect_facing_product(persona)
    reserved_names = frozenset(
        x.strip().lower()
        for x in (
            getattr(persona, "agent_name", "") or "",
            product,
            "navigator",
            "navigator ai",
        )
        if x and str(x).strip()
    )
    questions = intake_questions(lang=lang, product_name=product)

    hello = greet_line(
        persona,
        prefill.get("name", ""),
        lang=lang,
        agent_gender=agent_gender,
    )
    _say(speaker, hello)

    for key, question, default in questions:
        prefilled = (prefill.get(key) or "").strip()
        if prefilled:
            cleaned = _clean_field(key, prefilled, reserved_names=reserved_names)
            answers[key] = _answer_or_empty(key, cleaned)
            print(f"[intake] {key}={answers[key]!r} (prefilled)", flush=True)
            if key == "name" and answers.get("name") and answers["name"] != "there":
                _say(speaker, name_ack_line(answers["name"], lang=lang))
            continue
        _say(speaker, question)
        if listen is not None:
            print(f"[intake] listening for {key}…", flush=True)
            heard = ""
            try:
                heard = (listen(question) or "").strip()
            except Exception as exc:  # noqa: BLE001
                print(f"[intake] listen failed ({exc})", flush=True)
            if heard:
                lang = _maybe_switch_language(
                    speaker,
                    heard,
                    current=lang,
                    extra_languages=extra_languages,
                )
                questions = intake_questions(lang=lang, product_name=product)
            if is_declined(heard):
                answers[key] = ""
                print(f"[intake] {key} skipped (declined)", flush=True)
            else:
                if not heard:
                    cleaned = ""
                elif fast_extract:
                    cleaned = _clean_field(
                        key, heard, reserved_names=reserved_names, question=question
                    )
                else:
                    cleaned = extract_intake_entity(
                        key, question, heard, reserved_names=reserved_names
                    )
                answers[key] = _answer_or_empty(key, cleaned)
                print(
                    f"[intake] {key}={answers[key]!r}"
                    + ("" if heard else " (silence — not inventing an answer)"),
                    flush=True,
                )
                # Only acknowledge a name the prospect actually spoke.
                if (
                    key == "name"
                    and heard
                    and answers.get("name")
                    and answers["name"] != "there"
                ):
                    _say(speaker, name_ack_line(answers["name"], lang=lang))
        elif interactive:
            try:
                typed = input(f"[intake {key}] > ").strip()
            except EOFError:
                typed = ""
            if is_declined(typed):
                answers[key] = ""
            else:
                answers[key] = _answer_or_empty(
                    key,
                    _clean_field(key, typed, reserved_names=reserved_names)
                    if typed
                    else "",
                )
            if (
                key == "name"
                and typed
                and answers.get("name")
                and answers["name"] != "there"
            ):
                _say(speaker, name_ack_line(answers["name"], lang=lang))
        else:
            answers[key] = _answer_or_empty(key, _clean_field(key, default) or default)
            print(f"[intake] (non-interactive) {key}={answers[key]!r}", flush=True)

        # Listen/interactive paths already ack when appropriate; non-interactive skip.

    intake = ProspectIntake(
        name=answers.get("name", ""),
        company=answers.get("company", ""),
        business_type=answers.get("business_type", ""),
        looking_for=answers.get("looking_for", ""),
    )
    pitch = pitch_line(
        persona,
        intake,
        lang=lang,
        agent_gender=agent_gender,
        will_share_screen=will_share_screen,
    )
    _say(speaker, pitch)
    return intake, lang


def _maybe_switch_language(
    speaker: Speaker,
    utterance: str,
    *,
    current: SpokenLanguage,
    extra_languages: tuple[SpokenLanguage, ...],
) -> SpokenLanguage:
    from navigator.voice.language import apply_language_switch, apply_to_speakers

    allowed = frozenset({current, *extra_languages})

    def _on_switch(lang: SpokenLanguage) -> None:
        apply_to_speakers(lang, speaker)
        local = getattr(speaker, "local", None)
        apply_to_speakers(lang, local)

    new_lang, ack = apply_language_switch(
        utterance=utterance,
        current=current,
        on_switch=_on_switch,
        allowed=allowed,
    )
    if ack:
        _say(speaker, ack)
    return new_lang


def intake_questions(
    *, lang: SpokenLanguage = "en", product_name: str = "the product"
) -> tuple[tuple[str, str, str], ...]:
    from navigator.meeting.intake_copy import intake_questions as _q

    return _q(lang=lang, product_name=product_name)


def _answer_or_empty(key: str, cleaned: str) -> str:
    """Keep empty when unknown; name alone falls back to 'there' for speak-back."""
    if cleaned:
        return cleaned
    if key == "name":
        return "there"
    return ""


def _clean_field(
    key: str,
    value: str,
    *,
    reserved_names: frozenset[str] | None = None,
    question: str = "",
) -> str:
    if key == "name":
        return clean_name(value, reserved=reserved_names)
    if key == "company":
        return clean_company(value)
    if key == "business_type":
        return clean_business(value)
    if key == "looking_for":
        # Drop STT that is just a replay/paraphrase of the question we asked.
        from navigator.meeting.intake_clean import is_likely_bot_echo

        if question and is_likely_bot_echo(value, question):
            return ""
        return summarize_need(value, max_len=120) or clean_phrase(value)
    return clean_phrase(value)


def extract_intake_entity(
    key: str,
    question: str,
    heard: str,
    *,
    reserved_names: frozenset[str] | None = None,
) -> str:
    from navigator.agent.providers import get_provider
    try:
        provider = get_provider()
    except RuntimeError as e:
        print(f"[intake] LLM fallback due to: {e}")
        return _clean_field(key, heard, reserved_names=reserved_names, question=question)

    sys_prompt = f"""You are an extraction assistant for a sales call.
The agent asked: "{question}"
The user replied: "{heard}"

Your task is to extract ONLY the specific piece of information requested (e.g., just the company name, just the person's name, or just the business type).
If the user says they don't have one, or gives a negative response, output: "NONE"
If the user's answer is ambiguous or doesn't contain the requested information, output: "NONE"
If the transcript is clearly the agent talking to itself (bot name, product pitch, or a repeat of the question), output: "NONE"
Do not output full sentences. Only output the extracted entity. Do not use quotes."""

    try:
        result = provider.complete(system=sys_prompt, user="Extract the entity.")
        if not result or result.strip().upper() == "NONE":
            return ""
        if is_declined(result):
            return ""
        return _clean_field(
            key, result.strip(), reserved_names=reserved_names, question=question
        )
    except Exception as exc:
        msg = str(exc)
        if "429" in msg and "limit: 0" in msg:
            print(
                "[intake] Gemini quota is 0 — enable billing on your Google AI "
                "project or set NAVIGATOR_GROQ_API_KEY; using raw STT text",
                flush=True,
            )
        else:
            print(f"[intake] LLM extraction failed: {exc}", flush=True)
        return _clean_field(key, heard, reserved_names=reserved_names, question=question)


def _say(speaker: Speaker, text: str) -> None:
    print(f"[agent] {text}", flush=True)
    try:
        # Intake questions must be word-for-word. Live natural-mode rewrote
        # "What is your name?" into "My name is <product>." and then listened.
        speaker.say(text, mode="verbatim")  # type: ignore[call-arg]
    except TypeError:
        try:
            speaker.say(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[agent] TTS skipped: {exc}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] TTS skipped: {exc}", flush=True)
