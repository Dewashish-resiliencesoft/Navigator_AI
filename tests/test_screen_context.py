"""Screen context snapshot for planner."""

from navigator.automation.browser.screen_context import screen_snapshot


class _FakePage:
    url = "https://resiliohub.com/dashboard/"

    def title(self) -> str:
        return "ResilioHub Dashboard"

    def inner_text(self, _sel: str, timeout: int = 0) -> str:
        return "Inbox  Contacts  Flows  Analytics"


def test_screen_snapshot_compact():
    snap = screen_snapshot(_FakePage())  # type: ignore[arg-type]
    assert "url=https://resiliohub.com/dashboard/" in snap
    assert "title=ResilioHub Dashboard" in snap
    assert "Inbox" in snap
