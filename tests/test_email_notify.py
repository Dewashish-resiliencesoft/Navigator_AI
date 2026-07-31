"""Resend email notify."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from navigator.meeting.email_notify import send_meet_link_email


def test_send_meet_link_email_posts_resend_payload():
    captured: dict = {}
    real_request = __import__("urllib.request", fromlist=["Request"]).Request

    def capture(*args, **kwargs):
        req = real_request(*args, **kwargs)
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["auth"] = req.get_header("Authorization")
        return req

    fake = MagicMock()
    fake.status = 200
    fake.read.return_value = b'{"id":"email_123"}'
    fake.__enter__.return_value = fake
    fake.__exit__.return_value = False

    with patch("navigator.meeting.email_notify.Request", side_effect=capture):
        with patch("navigator.meeting.email_notify.urlopen", return_value=fake):
            eid = send_meet_link_email(
                api_key="re_test",
                to="you@example.com",
                meeting_url="https://meet.google.com/abc",
            )
    assert eid == "email_123"
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == "Bearer re_test"
    body = json.loads(captured["data"])
    assert body["to"] == ["you@example.com"]
    assert "meet.google.com/abc" in body["text"]
