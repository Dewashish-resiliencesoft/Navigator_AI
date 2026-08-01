"""ZAK callback for Attendee Zoom host join."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from navigator.app import main as app_module
from navigator.meeting.providers import MeetingProviderError, ZoomProvider
from navigator.core.settings import settings


@pytest.fixture
def client():
    return TestClient(app_module.app)


def test_zak_callback_returns_token(client, monkeypatch):
    monkeypatch.setattr(settings, "zoom_zak_callback_secret", "")
    monkeypatch.setattr(
        ZoomProvider,
        "fetch_zak",
        lambda self: "zak-from-zoom",
    )
    monkeypatch.setattr(
        "navigator.app.main.make_provider",
        lambda platform=None: ZoomProvider(
            account_id="a", client_id="c", client_secret="s"
        ),
    )
    resp = client.post(
        "/v1/zoom/zak",
        json={
            "bot_id": "b1",
            "callback_type": "zoom_tokens",
            "meeting_url": "https://zoom.us/j/1",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"zak_token": "zak-from-zoom"}


def test_zak_callback_rejects_bad_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "zoom_zak_callback_secret", "correct")
    resp = client.post("/v1/zoom/zak?secret=wrong", json={})
    assert resp.status_code == 401


def test_zak_callback_accepts_query_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "zoom_zak_callback_secret", "correct")
    monkeypatch.setattr(ZoomProvider, "fetch_zak", lambda self: "zak")
    monkeypatch.setattr(
        "navigator.app.main.make_provider",
        lambda platform=None: ZoomProvider(
            account_id="a", client_id="c", client_secret="s"
        ),
    )
    resp = client.post("/v1/zoom/zak?secret=correct", json={})
    assert resp.status_code == 200
    assert resp.json()["zak_token"] == "zak"


def test_zak_callback_maps_provider_failure(client, monkeypatch):
    monkeypatch.setattr(settings, "zoom_zak_callback_secret", "")

    def boom(self):
        raise MeetingProviderError("zoom down")

    monkeypatch.setattr(ZoomProvider, "fetch_zak", boom)
    monkeypatch.setattr(
        "navigator.app.main.make_provider",
        lambda platform=None: ZoomProvider(
            account_id="a", client_id="c", client_secret="s"
        ),
    )
    resp = client.post("/v1/zoom/zak", json={})
    assert resp.status_code == 502
    assert "zoom down" in resp.json()["detail"]


def test_zoom_zak_callback_url_embeds_secret(monkeypatch):
    from navigator.meeting.zoom_host import zoom_zak_callback_url

    monkeypatch.setattr(settings, "public_base_url", "https://tunnel.example")
    monkeypatch.setattr(settings, "zoom_zak_callback_secret", "s3cret")
    assert zoom_zak_callback_url() == (
        "https://tunnel.example/v1/zoom/zak?secret=s3cret"
    )


def test_require_live_settings_allows_empty_public_base_when_tunnel_bin_ok(
    monkeypatch,
):
    """Local Zoom can auto-tunnel; empty PUBLIC_BASE_URL is fine if tunnel_bin works."""
    from navigator.meeting.live_demo import _require_live_settings

    monkeypatch.setattr(settings, "attendee_base_url", "https://app.attendee.dev/api/v1")
    monkeypatch.setattr(settings, "attendee_api_key", "tok")
    monkeypatch.setattr(settings, "meeting_platform", "zoom")
    monkeypatch.setattr(settings, "public_base_url", "")
    monkeypatch.setattr(settings, "tunnel_bin", "cloudflared")
    _require_live_settings("https://zoom.us/j/1")  # no raise


def test_require_live_settings_demands_public_or_tunnel_for_zoom(monkeypatch):
    from navigator.meeting.live_demo import _require_live_settings

    monkeypatch.setattr(settings, "attendee_base_url", "https://app.attendee.dev/api/v1")
    monkeypatch.setattr(settings, "attendee_api_key", "tok")
    monkeypatch.setattr(settings, "meeting_platform", "zoom")
    monkeypatch.setattr(settings, "public_base_url", "")
    monkeypatch.setattr(settings, "tunnel_bin", "/no/such/cloudflared-binary")
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL|tunnel_bin"):
        _require_live_settings("https://zoom.us/j/1")
