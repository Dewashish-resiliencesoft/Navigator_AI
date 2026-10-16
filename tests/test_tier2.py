"""Phase 4: Tier 2 constrained live fallback (toggle-gated)."""

from __future__ import annotations

from uuid import uuid4

from navigator.agent.nodes.planning import planning
from navigator.agent.planner import HANDOFF_SPOKEN
from navigator.agent.state import CallDeps, initial_state
from navigator.automation.explore.guardrail import GuardrailVerdict
from navigator.core.schemas import ClickElement, Postcondition
from navigator.knowledge.context import RetrievalResult
from navigator.logs.decisions import DecisionTraceStore
from navigator.voice.tts import PrintSpeaker


def _empty_retrieve(query, product_id, **kw):
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


def _deps(site_graph, page, log, tmp_path, **kw):
    base = dict(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        product_id="acme",
        archive_dir=tmp_path / "archives",
        chroma_path=tmp_path / "chroma",
        groq_api_key=None,
        use_turn_brain=False,
        retrieve=_empty_retrieve,
        phrase=lambda **k: k["fallback"],
        decision_db_path=tmp_path / "decisions.db",
        pending_db_path=tmp_path / "pending.db",
        tier2_enabled=False,
    )
    base.update(kw)
    return CallDeps(**base)


def _walk(session=None):
    state = initial_state(
        session or uuid4(),
        "inbox",
        max_turns=5,
        walkthrough_flow_id="send_test_message",
    )
    state["phase"] = "walkthrough"
    state["walkthrough_step"] = 1
    state["transcript"] = ["user: where is the obscure feature nobody recorded?"]
    return state


def test_tier2_off_never_calls_proposer(site_graph, page, log, tmp_path):
    session = uuid4()
    called = {"n": 0}

    def propose(**kw):
        called["n"] += 1
        raise AssertionError("tier2 propose must not run when toggle off")

    deps = _deps(
        site_graph, page, log, tmp_path, tier2_enabled=False, tier2_propose=propose
    )
    out = planning(_walk(session), deps)
    assert called["n"] == 0
    assert out["pending_calls"] == []
    assert out["plan"].spoken_response == HANDOFF_SPOKEN
    with DecisionTraceStore(tmp_path / "decisions.db") as store:
        rows = store.for_session(session, "acme")
    assert rows[-1].branch == "handoff"


def test_tier2_refuses_mutating_target(site_graph, page, log, tmp_path):
    session = uuid4()

    def propose(**kw):
        return {
            "element": {"text": "Delete workspace", "label": "Delete workspace"},
            "call": ClickElement(
                selector="send_button",
                expects=Postcondition(check="visible", selector="composer"),
            ),
            "spoken": "I'll try deleting that.",
        }

    def classify(el, **kw):
        return GuardrailVerdict(True, "keyword:delete", "keyword")

    deps = _deps(
        site_graph,
        page,
        log,
        tmp_path,
        tier2_enabled=True,
        tier2_propose=propose,
        tier2_classify=classify,
    )
    out = planning(_walk(session), deps)
    assert out["pending_calls"] == []
    with DecisionTraceStore(tmp_path / "decisions.db") as store:
        rows = store.for_session(session, "acme")
    assert rows[-1].branch == "tier2_refused"


def test_tier2_safe_action_runs_without_pending_queue(
    site_graph, page, log, tmp_path
):
    session = uuid4()

    def propose(**kw):
        return {
            "element": {"text": "Open settings tab", "label": "Settings"},
            "call": ClickElement(
                selector="send_button",
                expects=Postcondition(check="visible", selector="composer"),
            ),
            "spoken": "I'll open Settings for you.",
        }

    def classify(el, **kw):
        return GuardrailVerdict(False, "read-only nav", "safe")

    deps = _deps(
        site_graph,
        page,
        log,
        tmp_path,
        tier2_enabled=True,
        tier2_propose=propose,
        tier2_classify=classify,
    )
    out = planning(_walk(session), deps)
    assert len(out["pending_calls"]) == 1
    assert out["pending_calls"][0].tool == "click_element"

    with DecisionTraceStore(tmp_path / "decisions.db") as store:
        rows = store.for_session(session, "acme")
    assert rows[-1].branch == "tier2_attempted"
