"""ActionLog: entries survive a round trip, and failures() is the REFLECTING input."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from navigator.logs.store import ActionLog
from navigator.schemas import (
    ActionLogEntry,
    ClickElement,
    FillField,
    Postcondition,
    ToolResult,
    VerifyResult,
)

TS = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def entry(session_id, *, ok=True, passed=True, verify=True, source="agent", tool="click"):
    call = (
        ClickElement(
            selector="send_button",
            expects=Postcondition(check="visible", selector="sent_bubble"),
        )
        if tool == "click"
        else FillField(
            selector="message_input",
            value="typed",
            source=source,
            expects=Postcondition(
                check="value_equals", selector="message_input", expected="typed"
            ),
        )
    )
    return ActionLogEntry(
        session_id=session_id,
        page="inbox",
        tool_call=call,
        expected_postcondition=call.expects,
        actual_result=ToolResult(ok=ok, tool=call.tool, detail="d", duration_ms=12),
        verify=VerifyResult(passed=passed, actual="a") if verify else None,
        source=source,
        timestamp=TS,
    )


def test_round_trip_preserves_the_tool_call_union(log):
    sid = uuid4()
    original = entry(sid, tool="fill", source="user")
    log.append(original)

    (loaded,) = log.entries(sid)
    assert loaded == original
    assert loaded.tool_call.tool == "fill_field"
    assert loaded.tool_call.value == "typed", "the union must rehydrate, not degrade"
    assert loaded.source == "user"


def test_failures_returns_only_failed_entries(log):
    sid = uuid4()
    log.append(entry(sid))  # clean
    log.append(entry(sid, ok=False, passed=False))  # action failed
    log.append(entry(sid, ok=True, passed=False))  # postcondition failed

    assert len(log.entries(sid)) == 3
    failures = log.failures(sid)
    assert len(failures) == 2
    assert all(f.failed for f in failures)


def test_action_failure_counts_as_failed_even_without_a_verify(log):
    sid = uuid4()
    log.append(entry(sid, ok=False, verify=False))
    (loaded,) = log.failures(sid)
    assert loaded.verify is None
    assert loaded.failed


def test_clean_entry_is_not_a_failure():
    assert not entry(uuid4()).failed


def test_sessions_are_isolated(log):
    a, b = uuid4(), uuid4()
    log.append(entry(a))
    log.append(entry(b, ok=False, passed=False))

    assert len(log.entries(a)) == 1
    assert log.failures(a) == []
    assert len(log.failures(b)) == 1
    assert set(log.sessions()) == {a, b}


def test_persists_across_connections(tmp_path):
    sid = uuid4()
    db = tmp_path / "persist.db"
    with ActionLog(db) as first:
        first.append(entry(sid))
    with ActionLog(db) as second:
        assert len(second.entries(sid)) == 1


def test_expected_postcondition_is_queryable_without_the_tool_call(log):
    """Reflection reads the postcondition column directly."""
    sid = uuid4()
    log.append(entry(sid, ok=True, passed=False))
    (loaded,) = log.failures(sid)
    assert loaded.expected_postcondition.check == "visible"
    assert loaded.expected_postcondition.selector == "sent_bubble"
