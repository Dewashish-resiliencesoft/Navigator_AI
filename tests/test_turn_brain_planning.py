"""Planning interrupt uses injected turn brain."""

from __future__ import annotations

from uuid import uuid4

from navigator.agent.nodes.planning import planning
from navigator.agent.state import CallDeps, initial_state
from navigator.agent.turn_brain import TurnDecision
from navigator.schemas import Navigate
from navigator.voice.tts import PrintSpeaker


def test_interrupt_turn_brain_navigates(site_graph, page, log, tmp_path):
    def fake_decide(**kwargs):
        return TurnDecision(
            intent="navigate_page",
            page_id="inbox",
            spoken_response="Taking you to the inbox.",
            nav_label=None,
            clean_intake=None,
        )

    deps = CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        product_id="acme",
        archive_dir=tmp_path / "archives",
        groq_api_key=None,
        decide_turn=fake_decide,
        use_turn_brain=True,
        choose_flow=lambda **k: (_ for _ in ()).throw(RuntimeError("should not Groq")),
    )
    # page.screenshot used by capture — Playwright page fixture should support it
    state = initial_state(uuid4(), "inbox", max_turns=5, walkthrough_flow_id="send_test_message")
    state = {
        **state,
        "phase": "walkthrough",
        "walkthrough_step": 1,
        "transcript": ["user: take me to the inbox"],
    }
    out = planning(state, deps)
    assert "inbox" in (out.get("plan").spoken_response or "").lower() or out.get(
        "pending_calls"
    )
    calls = out.get("pending_calls") or []
    assert calls and isinstance(calls[0], Navigate)
    assert calls[0].page_id == "inbox"
