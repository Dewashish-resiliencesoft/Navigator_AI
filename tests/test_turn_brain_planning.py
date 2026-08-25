"""Planning interrupt uses injected turn brain."""

from __future__ import annotations

from uuid import uuid4

from navigator.agent.nodes.planning import planning
from navigator.agent.state import CallDeps, initial_state
from navigator.agent.turn_brain import TurnDecision
from navigator.core.schemas import Navigate
from navigator.voice.tts import PrintSpeaker


def test_interrupt_turn_brain_navigates(site_graph, page, log, tmp_path):
    from navigator.knowledge.context import RetrievalResult

    def fake_decide(**kwargs):
        return TurnDecision(
            intent="navigate_page",
            page_id="inbox",
            spoken_response="Taking you to the inbox.",
            nav_label=None,
            clean_intake=None,
        )

    def empty_retrieve(query, product_id, **kw):
        return RetrievalResult(
            product_id=product_id,
            query=query,
            knowledge_chunks=[],
            candidate_flows=[],
            relevant_areas=[],
            knowledge_based_on_revision=None,
            current_published_revision=None,
            is_stale=False,
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
        retrieve=empty_retrieve,
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


def test_aligned_interrupt_skips_turn_brain_screenshot(site_graph, page, log, tmp_path):
    """On-script chatter must not take a PNG — Meet/Zoom share stays the video."""

    def boom(**kwargs):
        raise AssertionError("turn brain should not run for aligned utterance")

    deps = CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        product_id="acme",
        archive_dir=tmp_path / "archives",
        groq_api_key=None,
        decide_turn=boom,
        use_turn_brain=True,
        retrieve=lambda **k: (_ for _ in ()).throw(RuntimeError("no retrieve")),
        choose_flow=lambda **k: (_ for _ in ()).throw(RuntimeError("no choose")),
    )
    deps.speaker.say("Here is the send campaign button")
    state = initial_state(
        uuid4(), "inbox", max_turns=5, walkthrough_flow_id="send_test_message"
    )
    state = {
        **state,
        "phase": "walkthrough",
        "walkthrough_step": 1,
        "transcript": ["user: send campaign looks good"],
    }
    shots = {"n": 0}
    orig = page.screenshot

    def _count(*a, **k):
        shots["n"] += 1
        return orig(*a, **k)

    page.screenshot = _count  # type: ignore[method-assign]
    out = planning(state, deps)
    assert shots["n"] == 0
    assert out.get("phase") == "walkthrough"

