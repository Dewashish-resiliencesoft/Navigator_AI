"""Live phrasing: one Groq call per spoken turn.

Separate from `vision_narrator` on purpose. That module answers "what is on
screen"; this one answers "how should this turn sound, given what was just said
and what has already been covered". The walkthrough keeps using vision narration
for step description; the decision branches added in Phase 2 (clarifying
question, knowledge-only answer, handoff, resume) route through here, because
those lines were previously frozen constants.

Falls back to the caller's template on any failure. A demo that speaks a slightly
stiff line is fine; a demo that speaks nothing is not.
"""

from __future__ import annotations

from collections.abc import Callable

from navigator.agent.call_memory import CallMemory
from navigator.agent.speech_persona import speech_rules

def _phrasing_model() -> str:
    from navigator.core.settings import settings

    return settings.brain_phrasing_model

_SYSTEM = """You are a product specialist on a live voice demo. Write the single
line you say out loud right now.

Rules:
- 1-2 short sentences. Spoken English, natural contractions. No lists, no markdown.
- Never repeat a line you already said — vary the wording even for a similar step.
- Reference earlier moments naturally when relevant ("like we saw on the inbox").
- Never invent product features, prices, or brand names not given below.
- Do not mention being an AI, a screenshot, a flow, a confidence score, or a tool.
- Return ONLY the spoken line, with no quotes around it."""

#: What the line has to accomplish this turn.
INTENTS = {
    "clarify": "Ask ONE short clarifying question between the options given. Do not explain.",
    "answer": "Answer their question from the knowledge provided. Nothing beyond it.",
    "flow_intro": "Say what you're about to show them and why it fits what they asked.",
    "detour_intro": (
        "Acknowledge their question naturally — yes, you can show that — then "
        "transition smoothly into demonstrating it. Do NOT say you are starting a "
        "new flow or switching demos."
    ),
    "question_answered": (
        "Wrap up what you just showed and ask if their question is answered. "
        "One short sentence plus a check-in."
    ),
    "resume": "Bridge back to the walkthrough after the detour, then continue.",
    "resume_confirm": (
        "They confirmed the answer helped. Say briefly you'll continue the demo "
        "from where you paused."
    ),
    "resume_silence": (
        "They were quiet after your answer. Gently assume it helped, say you'll "
        "continue the demo, and invite questions anytime."
    ),
    "handoff": "Say warmly that this is outside what you can show, and a human will follow up.",
    "slow_down": "Offer to slow down or re-explain. Do not add new information.",
    "skip_ahead": "Acknowledge they want to move faster and say you'll skip ahead.",
}


def phrase_turn(
    *,
    intent: str,
    utterance: str = "",
    context: str = "",
    memory: CallMemory | None = None,
    pacing: str = "neutral",
    persona_name: str = "",
    product_brief: str = "",
    spoken_language: str = "en",
    agent_gender: str = "female",
    fallback: str,
    api_key: str | None = None,
    complete: Callable[[str], str] | None = None,
) -> str:
    """One natural spoken line, or `fallback` if the model is unavailable.

    `fallback` is required rather than defaulted: every caller already has a
    template line, and silently inventing one here would hide a broken key.
    """
    if complete is None and not (api_key or "").strip():
        return fallback

    prompt = build_prompt(
        intent=intent,
        utterance=utterance,
        context=context,
        memory=memory,
        pacing=pacing,
        persona_name=persona_name,
        product_brief=product_brief,
        spoken_language=spoken_language,
        agent_gender=agent_gender,
    )
    try:
        completer = complete or (lambda p: _groq_complete(api_key or "", p))
        line = (completer(prompt) or "").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[phrase] falling back ({exc})", flush=True)
        return fallback

    line = _clean(line)
    if len(line) < 3:
        return fallback
    return line


def build_prompt(
    *,
    intent: str,
    utterance: str = "",
    context: str = "",
    memory: CallMemory | None = None,
    pacing: str = "neutral",
    persona_name: str = "",
    product_brief: str = "",
    spoken_language: str = "en",
    agent_gender: str = "female",
) -> str:
    lines = [_SYSTEM, "", f"Your task this turn: {INTENTS.get(intent, intent)}"]
    lines.append(
        speech_rules(spoken_language=spoken_language, agent_gender=agent_gender)
    )
    if persona_name:
        lines.append(f"You are demoing: {persona_name}")
    lines.append(
        f"They just said: {utterance!r}" if utterance.strip()
        else "They said nothing — this continues the walkthrough."
    )
    if context.strip():
        lines += ["What you have to work with:", context.strip()[:2000]]
    if product_brief.strip():
        lines += ["Product brief (trim):", product_brief.strip()[:1200]]
    summary = memory.summary() if memory is not None else ""
    if summary:
        lines += ["Earlier on this call:", summary]
    if pacing in {"rushed", "confused"}:
        lines.append(
            "They seem to be in a hurry — be brief." if pacing == "rushed"
            else "They seem unsure — slow down and simplify."
        )
    lines.append("Your spoken line:")
    return "\n".join(lines)


def _clean(line: str) -> str:
    """Strip the wrappers an instruction-tuned model adds anyway."""
    line = line.strip()
    if line.startswith("```"):
        line = line.strip("`").strip()
    if len(line) >= 2 and line[0] == line[-1] and line[0] in {'"', "'"}:
        line = line[1:-1].strip()
    for prefix in ("Spoken line:", "You say:", "Line:"):
        if line.lower().startswith(prefix.lower()):
            line = line[len(prefix):].strip()
    return line


def _groq_complete(api_key: str, prompt: str) -> str:
    from navigator.core.groq_client import groq_client

    resp = groq_client(api_key).chat.completions.create(
        model=_phrasing_model(),
        messages=[{"role": "user", "content": prompt}],
        # Non-zero on purpose: identical consecutive narration is the bug this
        # layer exists to fix, and temperature 0 reproduces it exactly.
        temperature=0.7,
        max_tokens=120,
    )
    return resp.choices[0].message.content or ""
