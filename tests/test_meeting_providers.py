"""Meeting providers, with every HTTP call stubbed.

No test here may reach Google or Zoom. The stubs assert on the *request* as much
as the response, because the request body is where the bot-first guarantee lives:
Meet's accessType=OPEN and Zoom's waiting_room=False are what let Navigator into
a meeting with nobody there to admit it.
"""

from __future__ import annotations

import json

import pytest

from navigator.meeting import providers as mod
from navigator.meeting.providers import (
    GoogleMeetProvider,
    MeetingProviderError,
    StaticMeetingProvider,
    ZoomProvider,
    make_provider,
)


class _Calls(list):
    """Recorded requests, plus the queue of replies they get back."""

    replies: list


@pytest.fixture
def calls(monkeypatch):
    """Record every _post and reply from a queued script."""
    recorded = _Calls()
    recorded.replies = []

    def fake_post(url, *, headers, body):
        recorded.append({"url": url, "headers": headers, "body": body})
        reply = recorded.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(mod, "_post", fake_post)
    monkeypatch.setattr(mod, "_google_token", lambda sa, sub: f"tok:{sub}")
    return recorded


SPACE = {
    "name": "spaces/abc123",
    "meetingUri": "https://meet.google.com/aaa-bbbb-ccc",
    "meetingCode": "aaa-bbbb-ccc",
}


# -- google meet --------------------------------------------------------------


def google(**kw) -> GoogleMeetProvider:
    return GoogleMeetProvider(
        sa_json='{"type":"service_account"}', impersonate="demo@acme.test", **kw
    )


def test_spaces_creates_an_open_access_meeting(calls):
    calls.replies.append(SPACE)
    info = google().create_meeting("acme")

    assert info.url == "https://meet.google.com/aaa-bbbb-ccc"
    assert info.platform == "google_meet"
    assert info.provider_id == "spaces/abc123"
    assert info.open_access is True, "bot-first join depends on this"

    sent = calls[0]
    assert sent["url"] == mod.MEET_API
    assert sent["body"]["config"]["accessType"] == "OPEN", (
        "TRUSTED would hold Navigator in the waiting room until a human admits it"
    )
    assert sent["headers"]["Authorization"] == "Bearer tok:demo@acme.test"


def test_the_meeting_is_instant_not_scheduled(calls):
    """A "Show Demo" click has no start time -- nothing may be scheduled."""
    calls.replies.append(SPACE)
    google().create_meeting("acme")

    assert len(calls) == 1, "one call: create the space. No calendar round-trip."
    assert "calendar" not in calls[0]["url"], "no calendar event may be created"
    body = calls[0]["body"]
    assert set(body) == {"config"}, f"no time, no title, no attendees: {body}"


def test_only_the_space_scope_is_requested():
    """Narrow scope by design: we create spaces, we don't read anyone's calendar."""
    assert mod.GOOGLE_SCOPES == [
        "https://www.googleapis.com/auth/meetings.space.created"
    ]


def test_two_sessions_get_two_different_links(calls):
    second = dict(SPACE, name="spaces/def456", meetingUri="https://meet.google.com/ddd-eeee-fff")
    calls.replies.extend([SPACE, second])
    provider = google()
    a = provider.create_meeting("acme")
    b = provider.create_meeting("globex")
    assert a.url != b.url, "one link per session is the whole point"


def test_a_failed_create_raises_instead_of_falling_back(calls):
    """No silent fallback: a Calendar link would knock and wait, so we refuse."""
    calls.replies.append(MeetingProviderError("HTTP 403: Meet API disabled"))
    with pytest.raises(MeetingProviderError, match="Meet API disabled"):
        google().create_meeting("acme")
    assert len(calls) == 1


def test_a_response_without_a_uri_is_an_error_not_an_empty_url(calls):
    calls.replies.append({"name": "spaces/x"})
    with pytest.raises(MeetingProviderError, match="no URI"):
        google().create_meeting("acme")


def test_service_account_without_delegation_is_refused():
    """The failure mode worth naming: a key with no subject cannot create a Meet."""
    with pytest.raises(MeetingProviderError, match="delegation|impersonate"):
        mod._google_token('{"type":"service_account"}', "")


def test_missing_service_account_json_is_refused():
    with pytest.raises(MeetingProviderError, match="SA_JSON"):
        mod._google_token("", "demo@acme.test")


# -- zoom ---------------------------------------------------------------------


ZOOM_MEETING = {
    "id": 82312345678,
    "join_url": "https://zoom.us/j/82312345678?pwd=abc",
    "password": "s3cret",
}


def test_zoom_creates_a_meeting_for_host_first_join(calls):
    """Host (ZAK bot) starts the room; guests must not enter before host."""
    calls.replies.extend([{"access_token": "zt"}, ZOOM_MEETING])
    info = ZoomProvider(
        account_id="acc", client_id="cid", client_secret="sec"
    ).create_meeting("acme")

    assert info.url == "https://zoom.us/j/82312345678?pwd=abc"
    assert info.platform == "zoom"
    assert info.provider_id == "82312345678"
    assert info.passcode == "s3cret"
    assert info.open_access is True

    token_req, create_req = calls
    assert "grant_type=account_credentials" in token_req["url"]
    assert token_req["headers"]["Authorization"].startswith("Basic ")
    assert create_req["body"]["type"] == 1, (
        "type 1 = instant. Type 2 is scheduled and would need a start time."
    )
    assert "start_time" not in create_req["body"]
    settings_sent = create_req["body"]["settings"]
    assert settings_sent["join_before_host"] is False
    assert settings_sent["waiting_room"] is False


def test_zoom_fetch_zak_hits_user_token_endpoint(calls, monkeypatch):
    recorded = []

    def fake_get(url, *, headers):
        recorded.append({"url": url, "headers": headers})
        return {"token": "zak-abc"}

    monkeypatch.setattr(mod, "_get", fake_get)
    calls.replies.append({"access_token": "zt"})
    zak = ZoomProvider(
        account_id="acc", client_id="cid", client_secret="sec", user_id="me"
    ).fetch_zak()
    assert zak == "zak-abc"
    assert recorded[0]["url"] == f"{mod.ZOOM_API}/users/me/token?type=zak"
    assert recorded[0]["headers"]["Authorization"] == "Bearer zt"


def test_zoom_fetch_zak_without_token_is_an_error(calls, monkeypatch):
    monkeypatch.setattr(mod, "_get", lambda url, *, headers: {})
    calls.replies.append({"access_token": "zt"})
    with pytest.raises(MeetingProviderError, match="(?i)zak"):
        ZoomProvider(
            account_id="a", client_id="c", client_secret="s"
        ).fetch_zak()


def test_zoom_without_credentials_is_refused(calls):
    with pytest.raises(MeetingProviderError, match="ZOOM_ACCOUNT_ID"):
        ZoomProvider(account_id="", client_id="", client_secret="").create_meeting("a")


def test_zoom_response_without_join_url_is_an_error(calls):
    calls.replies.extend([{"access_token": "zt"}, {"id": 1}])
    with pytest.raises(MeetingProviderError, match="no join_url"):
        ZoomProvider(
            account_id="a", client_id="c", client_secret="s"
        ).create_meeting("acme")


# -- static + factory ---------------------------------------------------------


def test_static_returns_the_configured_url():
    info = StaticMeetingProvider("https://meet.google.com/fixed").create_meeting("a")
    assert info.url == "https://meet.google.com/fixed"
    assert info.platform == "static"
    assert info.open_access is False


def test_static_without_a_url_is_refused():
    with pytest.raises(MeetingProviderError, match="NAVIGATOR_MEETING_URL"):
        StaticMeetingProvider("").create_meeting("a")


def test_factory_reads_settings_and_honours_an_override(monkeypatch):
    from navigator.core.settings import settings

    monkeypatch.setattr(settings, "meeting_platform", "google_meet")
    monkeypatch.setattr(settings, "meeting_url", "https://meet.google.com/fixed")
    assert make_provider().platform == "google_meet"
    assert make_provider("zoom").platform == "zoom"
    assert make_provider("static").platform == "static"


def test_factory_rejects_an_unknown_platform():
    with pytest.raises(MeetingProviderError, match="unknown meeting platform"):
        make_provider("webex")  # type: ignore[arg-type]


def test_google_accepts_a_key_file_path(tmp_path, monkeypatch):
    """The env var may be a path or inline JSON; both must load the same info."""
    pytest.importorskip("google.oauth2.service_account")
    from google.oauth2 import service_account

    seen: dict = {}

    class FakeCreds:
        token = "t"

        def with_subject(self, sub):
            seen["subject"] = sub
            return self

        def refresh(self, _req):
            seen["refreshed"] = True

    monkeypatch.setattr(
        service_account.Credentials,
        "from_service_account_info",
        classmethod(
            lambda cls, info, scopes=None, **kw: (
                seen.update(info=info, scopes=scopes) or FakeCreds()
            )
        ),
    )

    key = tmp_path / "sa.json"
    key.write_text(json.dumps({"type": "service_account", "client_email": "x@y.iam"}))
    assert mod._google_token(str(key), "demo@acme.test") == "t"
    assert seen["info"]["client_email"] == "x@y.iam"
    assert seen["subject"] == "demo@acme.test"
    assert seen["refreshed"] is True
    assert "meetings.space.created" in " ".join(seen["scopes"])


def test_inline_json_and_a_path_load_the_same_credentials(tmp_path, monkeypatch):
    pytest.importorskip("google.oauth2.service_account")
    from google.oauth2 import service_account

    loaded: list[dict] = []

    class FakeCreds:
        token = "t"

        def with_subject(self, sub):
            return self

        def refresh(self, _req):
            pass

    monkeypatch.setattr(
        service_account.Credentials,
        "from_service_account_info",
        classmethod(lambda cls, info, scopes=None, **kw: (loaded.append(info) or FakeCreds())),
    )

    raw = json.dumps({"type": "service_account", "client_email": "x@y.iam"})
    key = tmp_path / "sa.json"
    key.write_text(raw)

    mod._google_token(raw, "demo@acme.test")
    mod._google_token(str(key), "demo@acme.test")
    assert loaded[0] == loaded[1]
