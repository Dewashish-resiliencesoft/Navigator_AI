"""Warm-pool Stage 2: claim matching (no Attendee / Meet)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from navigator.meeting.warm_pool import WarmPool, _WarmSlot


def _fake_handle(*, bot_in_meeting: bool = True, status: str = "running"):
    return SimpleNamespace(
        demo_id=uuid4(),
        product_id="prod_a",
        revision=3,
        session_id=uuid4(),
        origin="dashboard_test",
        status=status,
        page_id="home",
        actions=0,
        failures=0,
        error=None,
        meeting_url="https://meet.google.com/abc-defg-hij",
        platform="google_meet",
        said=[],
        bot_id="bot_1",
        bot_in_meeting=bot_in_meeting,
        leave_grace_remaining=None,
        language="en",
        language_code="en",
        language_confidence=1.0,
        current_narration="",
        speech_status="idle",
        started_at=None,
        finished_at=None,
        _stop=SimpleNamespace(is_set=lambda: False),
        public=lambda self=None: None,  # replaced below
    )


def test_claim_returns_view_when_ready_and_matching():
    pool = WarmPool()
    handle = _fake_handle()
    # DemoHandle.public-style dict for LiveDemoView construction.
    def public():
        return {
            "demo_id": handle.demo_id,
            "product_id": handle.product_id,
            "revision": handle.revision,
            "session_id": handle.session_id,
            "origin": handle.origin,
            "status": handle.status,
            "page_id": handle.page_id,
            "actions": 0,
            "failures": 0,
            "error": None,
            "said": [],
            "meeting_url": handle.meeting_url,
            "platform": handle.platform,
            "bot_in_meeting": True,
            "leave_grace_remaining": None,
            "language": "en",
            "language_code": "en",
            "language_confidence": 1.0,
            "current_narration": "",
            "speech_status": "idle",
        }

    handle.public = public
    meeting = SimpleNamespace(
        public=lambda: {
            "url": handle.meeting_url,
            "platform": "google_meet",
            "provider_id": "spaces/x",
            "passcode": "",
            "open_access": True,
        }
    )
    pool._warm_sessions["prod_a"] = _WarmSlot(
        product_id="prod_a",
        revision=3,
        page_id="home",
        flow_id="walkthrough",
        origin="dashboard_test",
        handle=handle,
        meeting=meeting,
        ready_at=1.0,
    )

    view = pool.claim(
        "prod_a",
        revision=3,
        page_id="home",
        flow_id="walkthrough",
        origin="dashboard_test",
    )
    assert view is not None
    assert view.demo_id == handle.demo_id
    assert view.bot_in_meeting is True
    assert view.meeting.url == handle.meeting_url
    assert "prod_a" not in pool._warm_sessions


def test_claim_misses_on_revision_or_flow_mismatch():
    pool = WarmPool()
    handle = _fake_handle()
    handle.public = lambda: {
        "demo_id": handle.demo_id,
        "product_id": "prod_a",
        "revision": 3,
        "session_id": handle.session_id,
        "origin": "dashboard_test",
        "status": "running",
        "page_id": "home",
        "actions": 0,
        "failures": 0,
        "meeting_url": handle.meeting_url,
        "platform": "google_meet",
        "bot_in_meeting": True,
    }
    meeting = SimpleNamespace(
        public=lambda: {
            "url": handle.meeting_url,
            "platform": "google_meet",
            "provider_id": "spaces/x",
            "passcode": "",
            "open_access": True,
        }
    )
    pool._warm_sessions["prod_a"] = _WarmSlot(
        product_id="prod_a",
        revision=3,
        page_id="home",
        flow_id="walkthrough",
        origin="dashboard_test",
        handle=handle,
        meeting=meeting,
        ready_at=1.0,
    )
    assert (
        pool.claim(
            "prod_a",
            revision=4,
            page_id="home",
            flow_id="walkthrough",
            origin="dashboard_test",
        )
        is None
    )
    assert (
        pool.claim(
            "prod_a",
            revision=3,
            page_id="home",
            flow_id="other",
            origin="dashboard_test",
        )
        is None
    )
    assert (
        pool.claim(
            "prod_a",
            revision=3,
            page_id="home",
            flow_id="walkthrough",
            origin="public_embed",
        )
        is None
    )
    assert "prod_a" in pool._warm_sessions
