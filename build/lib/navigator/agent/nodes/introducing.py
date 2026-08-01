"""INTRODUCING: the opener. Rendered from the product's persona, no LLM call.

The intro never needs to vary within a call, so spending a model call on it buys
nothing. It does need to vary *per product*, which is why the text comes from the
persona stored with the site graph rather than living here as a literal.
"""

from __future__ import annotations

from navigator.agent.state import CallDeps, CallState
from navigator.schemas import Persona


def render_intro(persona: Persona) -> str:
    positioning = f", {persona.one_liner}," if persona.one_liner else ""
    return (
        f"Hi everyone, I'm {persona.agent_name}, and I'll be walking you through "
        f"{persona.product_name}{positioning} today. I'm driving the real product "
        f"live, so feel free to give me your own data at any point and I'll type it "
        f"in so you can see what happens."
    )


def introducing(state: CallState, deps: CallDeps) -> CallState:
    line = render_intro(deps.graph.effective_persona())
    return CallState(narration=[line], transcript=[f"agent: {line}"])
