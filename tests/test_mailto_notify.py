"""mailto: Meet-link notifier."""

from __future__ import annotations

from unittest.mock import patch

from navigator.meeting.mailto_notify import build_mailto_url, notify_demo_link_mailto


def test_build_mailto_includes_to_and_meet_link():
    url = build_mailto_url(
        to="dewashishhatekar@resiliencesoft.com",
        meeting_url="https://meet.google.com/abc-defg-hij",
    )
    assert url.startswith("mailto:dewashishhatekar@resiliencesoft.com?")
    assert "meet.google.com" in url


def test_notify_opens_mailto_via_webbrowser_when_no_xdg():
    opened: list[str] = []
    with patch("navigator.meeting.mailto_notify.shutil.which", return_value=None):
        with patch(
            "navigator.meeting.mailto_notify.webbrowser.open",
            side_effect=opened.append,
        ):
            out = notify_demo_link_mailto(
                to="a@b.com",
                meeting_url="https://meet.google.com/xyz",
            )
    assert opened == [out]
    assert out.startswith("mailto:a@b.com?")
