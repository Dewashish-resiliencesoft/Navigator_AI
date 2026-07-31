"""Teams Incoming Webhook notifier."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from navigator.meeting.teams import notify_demo_link


def test_notify_posts_text():
    captured: dict = {}

    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["body"] = req.data
        m = MagicMock()
        m.status = 200
        m.read.return_value = b"1"
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        return m

    with patch("navigator.meeting.teams.urlopen", side_effect=fake_urlopen):
        notify_demo_link(
            webhook_url="https://example.webhook",
            meeting_url="https://meet.google.com/abc",
        )
    payload = json.loads(captured["body"])
    assert "Navigator demo" in payload["text"]
    assert "meet.google.com/abc" in payload["text"]
