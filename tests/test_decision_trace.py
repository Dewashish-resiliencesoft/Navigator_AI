"""DecisionTrace recording and queries.

Phase 1 built the store. Phase 2 wires live planning to write one row per turn
(`tests/test_live_decision.py`). These tests still prove the store round-trips
a hand-built sequence.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from navigator.logs.decisions import BRANCHES, DecisionTraceStore


@pytest.fixture
def store(tmp_path):
    with DecisionTraceStore(tmp_path / "decisions.db") as s:
        yield s


#: A plausible multi-turn call: default flow, a Topic detour, a knowledge answer,
#: an ambiguous ask, something out of scope, then goodbye.
TURNS = [
    dict(
        utterance="",
        branch="continuation",
        spoken="Here's the inbox — three conversations waiting.",
        detail="silence; advanced default flow to step 1",
    ),
    dict(
        utterance="can you show me how sending a message works",
        branch="flow_executed",
        spoken="Sure — let me send one now.",
        chosen_flow_id="send_message",
        flow_candidates=[("send_message", 0.91), ("view_contact", 0.22)],
        detail="high confidence 0.91 >= 0.75; paused default flow at step 1",
    ),
    dict(
        utterance="how much does the pro plan cost",
        branch="knowledge_only",
        spoken="Pro is billed per seat, monthly.",
        knowledge_hits=[("chunk_billing_01", 0.68)],
        detail="no flow match; knowledge hit 0.68; no tools executed",
    ),
    dict(
        utterance="what about the other thing",
        branch="clarifying_question",
        spoken="Do you mean reporting, or billing?",
        flow_candidates=[("reporting", 0.51), ("billing", 0.48)],
        detail="medium confidence 0.51; asked before running anything",
    ),
    dict(
        utterance="can you delete my competitor's account",
        branch="handoff",
        spoken="That's outside what I can show here.",
        detail="nothing relevant found",
    ),
    dict(
        utterance="thanks, that's all",
        branch="ended",
        spoken="Thanks for your time!",
        detail="goodbye detected",
    ),
]


def _record_call(store, product_id: str, session_id) -> None:
    for turn in TURNS:
        store.record(product_id=product_id, session_id=session_id, **turn)


def test_records_one_entry_per_turn_in_order(store):
    session_id = uuid4()
    _record_call(store, "acme", session_id)

    rows = store.for_session(session_id)
    assert len(rows) == len(TURNS), "one entry per turn, no gaps"
    assert [r.branch for r in rows] == [t["branch"] for t in TURNS], "oldest first"


def test_captures_verbatim_utterance_and_silence(store):
    session_id = uuid4()
    _record_call(store, "acme", session_id)
    rows = store.for_session(session_id)

    assert rows[0].was_silent, "empty utterance is the silence/continuation marker"
    assert rows[1].utterance == "can you show me how sending a message works", (
        "utterance stored verbatim, not normalised"
    )
    assert not rows[1].was_silent


def test_captures_retrieval_and_branch_reasoning(store):
    session_id = uuid4()
    _record_call(store, "acme", session_id)
    rows = store.for_session(session_id)

    flow_turn = rows[1]
    assert flow_turn.chosen_flow_id == "send_message"
    assert flow_turn.flow_candidates == (("send_message", 0.91), ("view_contact", 0.22))
    assert "0.91" in flow_turn.detail

    knowledge_turn = rows[2]
    assert knowledge_turn.chosen_flow_id is None, "knowledge-only ran no flow"
    assert knowledge_turn.knowledge_hits == (("chunk_billing_01", 0.68),)
    assert knowledge_turn.spoken == "Pro is billed per seat, monthly."


def test_scoped_per_session(store):
    """Two calls on one product don't bleed into each other's traces."""
    first, second = uuid4(), uuid4()
    _record_call(store, "acme", first)
    store.record(
        product_id="acme",
        session_id=second,
        utterance="different call",
        branch="handoff",
        spoken="...",
    )

    assert len(store.for_session(first)) == len(TURNS)
    assert len(store.for_session(second)) == 1


def test_scoped_per_product(store):
    """A session id read under the wrong product returns nothing."""
    session_id = uuid4()
    _record_call(store, "acme", session_id)

    assert store.for_session(session_id, product_id="acme")
    assert store.for_session(session_id, product_id="other") == [], (
        "cross-tenant read must not return another product's trace"
    )
    assert store.for_product("other") == []


def test_query_product_by_branch(store):
    """The read that matters for review: every handoff across a product's calls."""
    _record_call(store, "acme", uuid4())
    _record_call(store, "acme", uuid4())

    handoffs = store.for_product("acme", branch="handoff")
    assert len(handoffs) == 2, "one handoff per call"
    assert {r.branch for r in handoffs} == {"handoff"}

    assert len(store.for_product("acme")) == 2 * len(TURNS)
    assert len(store.for_product("acme", limit=3)) == 3


def test_survives_reopen(store, tmp_path):
    """Written rows are durable, not just in this connection."""
    session_id = uuid4()
    _record_call(store, "acme", session_id)

    with DecisionTraceStore(tmp_path / "decisions.db") as reopened:
        assert len(reopened.for_session(session_id)) == len(TURNS)


def test_records_unknown_branch_rather_than_dropping_it(store):
    """A later phase's new branch string must still be recorded."""
    session_id = uuid4()
    row = store.record(
        product_id="acme",
        session_id=session_id,
        utterance="something novel",
        branch="phase_9_experiment",
        spoken="...",
    )
    assert row.branch not in BRANCHES
    assert store.for_session(session_id)[0].branch == "phase_9_experiment"


def test_known_branches_all_round_trip(store):
    """Every documented branch is storable and readable back."""
    session_id = uuid4()
    for branch in sorted(BRANCHES):
        store.record(
            product_id="acme",
            session_id=session_id,
            utterance=f"utterance for {branch}",
            branch=branch,
            spoken=f"spoken for {branch}",
        )
    assert {r.branch for r in store.for_session(session_id)} == BRANCHES
