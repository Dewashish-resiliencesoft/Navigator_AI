"""Phase 2: live retrieve_context branching on planning interrupts.

Integration-level: planning() end-to-end with injected retrieve/phrase, not
score_flows in isolation.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from navigator.agent.call_memory import CallMemory
from navigator.agent.nodes.planning import planning
from navigator.agent.planner import HANDOFF_SPOKEN
from navigator.agent.state import CallDeps, initial_state
from navigator.knowledge.context import KnowledgeChunk, RetrievalResult
from navigator.logs.decisions import DecisionTraceStore
from navigator.voice.tts import PrintSpeaker


def _empty_result(query: str, product_id: str, **_kw) -> RetrievalResult:
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


def _result(
    query: str,
    product_id: str,
    *,
    flows: list[tuple[str, float]] | None = None,
    knowledge: list[tuple[KnowledgeChunk, float]] | None = None,
) -> RetrievalResult:
    return RetrievalResult(
        product_id=product_id,
        query=query,
        knowledge_chunks=list(knowledge or []),
        candidate_flows=list(flows or []),
        relevant_areas=[],
        knowledge_based_on_revision=None,
        current_published_revision=None,
        is_stale=False,
    )


def _chunk(text: str, *, chunk_id: str = "k1") -> KnowledgeChunk:
    return KnowledgeChunk(
        id=chunk_id,
        product_id="acme",
        text=text,
        category="faq",
        summary="pricing",
        revision_tied_to=1,
        created_at="",
    )


def _deps(site_graph, page, log, tmp_path, **kw) -> CallDeps:
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
        decide_turn=None,
        choose_flow=lambda **k: (_ for _ in ()).throw(
            AssertionError("choose_flow must not run on live decision path")
        ),
        decision_db_path=tmp_path / "decisions.db",
        memory=CallMemory(),
    )
    base.update(kw)
    return CallDeps(**base)


def _walk_state(**extra):
    state = initial_state(
        uuid4(), "inbox", max_turns=10, walkthrough_flow_id="send_test_message"
    )
    state["phase"] = "walkthrough"
    state["walkthrough_step"] = 2
    state["walkthrough_page_id"] = "inbox"
    state.update(extra)
    return state


def test_high_confidence_flow_match_switches_execution(
    site_graph, page, log, tmp_path
):
    def retrieve(query, product_id, **kw):
        return _result(query, product_id, flows=[("search_contact", 0.72)])

    lines: list[str] = []

    def phrase(**kw):
        line = f"Let me show search ({len(lines)})"
        lines.append(line)
        return line

    deps = _deps(site_graph, page, log, tmp_path, retrieve=retrieve, phrase=phrase)
    state = _walk_state(
        transcript=["user: how do I search for a contact?"],
    )
    out = planning(state, deps)
    assert [c.tool for c in out["pending_calls"]] == ["fill_field", "click_element"]
    assert out["walkthrough_step"] == 2
    assert out.get("resume_step") == 2
    assert out.get("resume_page_id") == "inbox"
    assert "search" in (out["plan"].spoken_response or "").lower()

    with DecisionTraceStore(tmp_path / "decisions.db") as store:
        rows = store.for_session(state["session_id"], product_id="acme")
    assert len(rows) == 1
    assert rows[0].branch == "flow_executed"
    assert rows[0].chosen_flow_id == "search_contact"
    assert rows[0].flow_candidates[0][0] == "search_contact"


def test_knowledge_only_answer_has_zero_tool_calls(site_graph, page, log, tmp_path):
    chunk = _chunk("Pricing starts at $29/seat/month for teams.")

    def retrieve(query, product_id, **kw):
        return _result(query, product_id, flows=[("send_test_message", 0.12)], knowledge=[(chunk, 0.61)])

    deps = _deps(
        site_graph,
        page,
        log,
        tmp_path,
        retrieve=retrieve,
        phrase=lambda **kw: "Pricing starts at twenty-nine a seat.",
    )
    state = _walk_state(transcript=["user: how does pricing work?"])
    out = planning(state, deps)
    assert out["pending_calls"] == []
    assert out["walkthrough_step"] == 2
    assert "pricing" in (out["plan"].spoken_response or "").lower() or "twenty-nine" in (
        out["plan"].spoken_response or ""
    ).lower()

    with DecisionTraceStore(tmp_path / "decisions.db") as store:
        rows = store.for_session(state["session_id"], product_id="acme")
    assert rows[0].branch == "knowledge_only"
    assert rows[0].chosen_flow_id is None


def test_medium_confidence_asks_then_runs_on_yes(site_graph, page, log, tmp_path):
    def retrieve(query, product_id, **kw):
        return _result(query, product_id, flows=[("search_contact", 0.42)])

    deps = _deps(
        site_graph,
        page,
        log,
        tmp_path,
        retrieve=retrieve,
        phrase=lambda **kw: kw["fallback"],
    )
    state = _walk_state(transcript=["user: something about finding people?"])
    out = planning(state, deps)
    assert out["pending_calls"] == []
    assert out.get("awaiting_confirm_flow_id") == "search_contact"

    with DecisionTraceStore(tmp_path / "decisions.db") as store:
        assert store.for_session(state["session_id"], "acme")[0].branch == "clarifying_question"

    # Prospect confirms
    state2 = {
        **state,
        **{k: out[k] for k in out if k != "transcript"},
        "transcript": list(state["transcript"]) + list(out.get("transcript") or []) + ["user: yes"],
        "awaiting_confirm_flow_id": out["awaiting_confirm_flow_id"],
        "walkthrough_step": out["walkthrough_step"],
    }
    out2 = planning(state2, deps)
    assert [c.tool for c in out2["pending_calls"]] == ["fill_field", "click_element"]
    assert out2.get("awaiting_confirm_flow_id") in (None, "")


def test_default_flow_resumes_after_detour(site_graph, page, log, tmp_path):
    def retrieve(query, product_id, **kw):
        return _result(query, product_id, flows=[("search_contact", 0.70)])

    deps = _deps(
        site_graph,
        page,
        log,
        tmp_path,
        retrieve=retrieve,
        phrase=lambda **kw: kw["fallback"],
    )
    state = _walk_state(transcript=["user: show me contact search"])
    detour = planning(state, deps)
    assert detour.get("resume_step") == 2

    # Silence → walkthrough continues from remembered step
    resume_state = {
        **state,
        "walkthrough_step": detour["walkthrough_step"],
        "resume_step": detour.get("resume_step"),
        "resume_page_id": detour.get("resume_page_id"),
        "transcript": [],  # silence / continuation
        "pending_calls": [],
    }
    cont = planning(resume_state, deps)
    assert cont.get("phase") == "walkthrough"
    assert cont["walkthrough_step"] == 3  # advanced one from resume 2
    assert cont.get("resume_step") is None
    assert len(cont["pending_calls"]) == 1


def test_consecutive_phrasing_lines_are_not_identical(site_graph, page, log, tmp_path):
    n = {"i": 0}

    def retrieve(query, product_id, **kw):
        return _result(query, product_id, flows=[], knowledge=[(_chunk("Teams share one inbox."), 0.7)])

    def phrase(**kw):
        n["i"] += 1
        return f"Spoken variant number {n['i']} about the shared inbox."

    deps = _deps(site_graph, page, log, tmp_path, retrieve=retrieve, phrase=phrase)
    s1 = _walk_state(transcript=["user: how does the inbox work?"])
    o1 = planning(s1, deps)
    s2 = _walk_state(
        session_id=s1["session_id"],
        transcript=["user: tell me more about the inbox"],
    )
    # Same memory instance so phrasing sees prior lines
    o2 = planning(s2, deps)
    assert o1["plan"].spoken_response != o2["plan"].spoken_response


def test_multi_turn_call_writes_full_decision_trace(site_graph, page, log, tmp_path):
    session = uuid4()
    calls = {"n": 0}

    def retrieve(query, product_id, **kw):
        calls["n"] += 1
        if "search" in query.lower() or "contact" in query.lower():
            return _result(query, product_id, flows=[("search_contact", 0.68)])
        if "pric" in query.lower():
            return _result(
                query,
                product_id,
                flows=[("send_test_message", 0.05)],
                knowledge=[(_chunk("Plans start free."), 0.55)],
            )
        return _empty_result(query, product_id)

    deps = _deps(
        site_graph,
        page,
        log,
        tmp_path,
        retrieve=retrieve,
        phrase=lambda **kw: kw["fallback"],
    )

    # Turn 1: high flow
    s1 = _walk_state(session_id=session, transcript=["user: show contact search"])
    planning(s1, deps)

    # Turn 2: knowledge
    s2 = _walk_state(session_id=session, transcript=["user: how does pricing work?"])
    planning(s2, deps)

    # Turn 3: nothing → handoff
    s3 = _walk_state(session_id=session, transcript=["user: can you integrate with martian CRM?"])
    out3 = planning(s3, deps)
    assert out3["pending_calls"] == []
    assert HANDOFF_SPOKEN in (out3["plan"].spoken_response or "") or "follow" in (
        out3["plan"].spoken_response or ""
    ).lower() or "human" in (out3["plan"].spoken_response or "").lower()

    # Turn 4: continuation
    s4 = _walk_state(session_id=session, transcript=[], walkthrough_step=2)
    planning(s4, deps)

    with DecisionTraceStore(tmp_path / "decisions.db") as store:
        rows = store.for_session(session, product_id="acme")
    branches = [r.branch for r in rows]
    assert branches == ["flow_executed", "knowledge_only", "handoff", "continuation"]
