"""Pending correction approve → Chroma."""

from __future__ import annotations

from navigator.memory.pending import PendingCorrectionStore
from navigator.memory.retrieval import retrieve_corrections
from navigator.memory.seed import seed_correction


def test_approve_flow_seeds_chroma(tmp_path):
    db = tmp_path / "p.db"
    chroma = tmp_path / "chroma"
    with PendingCorrectionStore(db) as store:
        row = store.add(
            product_id="acme",
            session_id="s1",
            page="inbox",
            tool_call_type="click_element",
            rule="Wait for toast after send",
            source_call_id="c1",
        )
        assert row.status == "pending"
        seed_correction(
            chroma,
            product_id="acme",
            rule=row.rule,
            page=row.page,
            tool_call_type=row.tool_call_type,
            source_call_id=row.source_call_id,
            doc_id=row.id,
        )
        updated = store.set_status(row.id, "acme", "approved")
        assert updated is not None
        assert updated.status == "approved"
        assert store.list_pending("acme") == []

    hits = retrieve_corrections("acme", "toast send", page="inbox", path=chroma)
    assert any("toast" in h.rule.lower() for h in hits)


def test_reject_removes_from_pending(tmp_path):
    db = tmp_path / "p.db"
    with PendingCorrectionStore(db) as store:
        row = store.add(
            product_id="acme",
            session_id="s1",
            page="inbox",
            tool_call_type="click_element",
            rule="x",
            source_call_id="c1",
        )
        store.set_status(row.id, "acme", "rejected")
        assert store.list_pending("acme") == []
        got = store.get(row.id, "acme")
        assert got is not None and got.status == "rejected"
