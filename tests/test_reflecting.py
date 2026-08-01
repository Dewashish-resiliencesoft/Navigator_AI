"""Reflection + pending corrections."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from navigator.agent.nodes.reflecting import classify_correction, reflecting
from navigator.agent.state import CallDeps, initial_state
from navigator.knowledge.memory.pending import PendingCorrectionStore
from navigator.core.schemas import (
    ActionLogEntry,
    ClickElement,
    Postcondition,
    ToolResult,
    VerifyResult,
)
from navigator.voice.tts import PrintSpeaker


class FakeProvider:
    def complete(self, system: str, user: str) -> str:
        return "Always wait for the composer before clicking send."

    def complete_with_image(self, system: str, user: str, png: bytes) -> str:
        return "PASSED\nLooks good"


def _failure_entry(session_id, product_id="acme"):
    call = ClickElement(
        selector="send_button",
        expects=Postcondition(check="visible", selector="toast"),
    )
    return ActionLogEntry(
        session_id=session_id,
        product_id=product_id,
        page="inbox",
        tool_call=call,
        expected_postcondition=call.expects,
        actual_result=ToolResult(ok=True, tool="click_element", duration_ms=1),
        verify=VerifyResult(passed=False, actual="missing"),
        timestamp=datetime.now(timezone.utc),
    )


def test_reflecting_writes_pending_rule(site_graph, page, log, tmp_path):
    session = uuid4()
    state = initial_state(session, "inbox")
    state["failures"] = [_failure_entry(session)]
    pending_path = tmp_path / "pending.db"
    deps = CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        product_id="acme",
        archive_dir=tmp_path / "archives",
        reflect_provider=FakeProvider(),
        pending_db_path=pending_path,
    )
    reflecting(state, deps)
    with PendingCorrectionStore(pending_path) as store:
        rows = store.list_pending("acme")
    assert len(rows) == 1
    assert "composer" in rows[0].rule


def test_classify_correction_yes():
    assert classify_correction(
        "no, click the other button",
        None,
        complete=lambda _p: "yes",
    )


def test_classify_correction_no():
    assert not classify_correction(
        "show me how to send",
        None,
        complete=lambda _p: "no",
    )
