"""Vision-first narration: the agent looks at the screen and speaks.

The agent is a product manager giving a live demo. It sees a screenshot,
knows what the product is about (from the brief), knows who the prospect
is (from intake), and has a YAML hint about what this step is about.
It generates natural narration from what it SEES, not from a script.
"""

from __future__ import annotations

import json
from collections.abc import Callable

_SYSTEM = """You are a product specialist giving a live screen-share demo.
You can SEE the product on screen via a screenshot. You are narrating a live
walkthrough to a prospect.

Your job:
1. Look at the screenshot and describe what's relevant to the current step.
2. Reference visible UI elements naturally (e.g. "you can see the three conversations on the left").
3. Use the narration hint as a GUIDE, but generate your own words based on what you actually see.
4. When section knowledge is provided, explain how this part of the product works in one short beat — grounded in that knowledge only.
5. Personalize with the prospect's name/company/need when it's natural.
6. SPEAK SLOWLY AND NATURALLY. Keep your sentences very short (10-15 words max).
7. Use natural pauses by inserting commas, periods, or em-dashes (—). This helps the text-to-speech engine pace your voice like a real human.
8. Sound warm, relaxed, and conversational. Do not sound like a robot reading documentation or a feature list.
9. Never invent features not present in the brief, section knowledge, or screenshot.
10. Never mention you're an AI or that you're looking at a screenshot. Just narrate as if you're driving the product.

Return ONLY the narration text. No JSON, no markdown, no quotes."""


def generate_narration(
    *,
    screenshot_png: bytes,
    screen_text: str,
    narration_hint: str = "",
    intake_summary: str = "",
    product_brief: str = "",
    step_action: str = "",
    section_knowledge: str = "",
    complete_with_image: Callable[[str, str, bytes], str] | None = None,
) -> str:
    """Generate narration from what the agent sees on screen.

    Args:
        screenshot_png: Current Playwright screenshot.
        screen_text: Visible text from DOM (url + title + body text).
        narration_hint: YAML spoken field — a guide, not a script.
        intake_summary: Prospect info from intake.
        product_brief: Product context loaded from knowledge base.
        step_action: What tool is about to execute (e.g. "click send_button").
        section_knowledge: Knowledge chunks for this page/section — explain how
            this part of the product works while showing it.
        complete_with_image: LLM vision call. Defaults to Gemini provider.

    Returns:
        Natural narration string the agent should speak aloud.
    """
    if complete_with_image is None:
        from navigator.agent.providers import get_provider
        complete_with_image = get_provider().complete_with_image

    user_parts = [
        f"Narration hint (guide only, generate your own words): {narration_hint or '(none)'}",
        f"What I'm about to do: {step_action or '(continue walkthrough)'}",
        f"Prospect: {intake_summary or '(unknown prospect)'}",
        f"Product brief: {(product_brief or '')[:1500]}",
        f"Section knowledge (use this to explain how this part works; do not invent beyond it): {(section_knowledge or '')[:2000] or '(none)'}",
        f"Visible text: {(screen_text or '')[:1000]}",
        "Generate the narration I should speak right now. Voice only, 2-3 sentences.",
        "If section knowledge is present, weave in one concrete point about how this feature works.",
    ]
    user = "\n".join(user_parts)

    try:
        narration = complete_with_image(_SYSTEM, user, screenshot_png).strip()
        if not narration or len(narration) < 5:
            # Vision failed to produce useful output — fall back to hint.
            return narration_hint or "Let me show you this."
        # Strip accidental JSON or markdown wrapping.
        if narration.startswith('"') and narration.endswith('"'):
            try:
                narration = json.loads(narration)
            except json.JSONDecodeError:
                pass
        if narration.startswith("```"):
            narration = narration.strip("`").strip()
        return narration
    except Exception as exc:  # noqa: BLE001
        print(f"[narrate] vision generation failed ({exc}); using hint", flush=True)
        return narration_hint or "Let me walk you through this."
