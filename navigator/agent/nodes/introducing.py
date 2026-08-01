"""INTRODUCING: the opener. Rendered from the product's persona (+ intake).

The intro never needs an LLM call. It varies per product (persona) and per
prospect (intake name / need) so the walkthrough feels personal from the start.
"""

from __future__ import annotations

from navigator.agent.state import CallDeps, CallState
from navigator.meeting.intake import ProspectIntake
from navigator.schemas import Persona


def render_intro(
    persona: Persona, intake: ProspectIntake | None = None
) -> str:
    positioning = f", {persona.one_liner}," if persona.one_liner else ""
    if intake and (intake.name or intake.looking_for):
        name = intake.name.strip() or "there"
        need = intake.looking_for.strip() or "what you care about"
        company = intake.company.strip() or "your team"
        return (
            f"{name}, I'm {persona.agent_name} — walking you through "
            f"{persona.product_name}{positioning} with {company} in mind. "
            f"You mentioned {need}, so I'll drive the real product live around that. "
            f"Jump in with your own data anytime and I'll type it in."
        )
    return (
        f"Hi everyone, I'm {persona.agent_name}, and I'll be walking you through "
        f"{persona.product_name}{positioning} today. I'm driving the real product "
        f"live, so feel free to give me your own data at any point and I'll type it "
        f"in so you can see what happens."
    )


def introducing(state: CallState, deps: CallDeps) -> CallState:
    line = render_intro(deps.graph.effective_persona(), deps.intake)
    return CallState(narration=[line], transcript=[f"agent: {line}"])
