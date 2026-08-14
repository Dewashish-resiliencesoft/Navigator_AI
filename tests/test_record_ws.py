"""Local-record WS target: Platform env only, never Client-supplied (SSRF)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from navigator.app.main import RecordStartBody
from navigator.automation import record_ws


def test_empty_is_server_launch():
    assert record_ws.safe_record_ws_url("") == ""
    assert record_ws.safe_record_ws_url("   ") == ""


def test_rfc1918_with_port_ok():
    assert (
        record_ws.safe_record_ws_url("ws://192.168.1.50:3333")
        == "ws://192.168.1.50:3333"
    )
    assert (
        record_ws.safe_record_ws_url("ws://10.0.0.8:3333/rec-token")
        == "ws://10.0.0.8:3333/rec-token"
    )


def test_rejects_loopback_and_metadata():
    for bad in (
        "ws://127.0.0.1:3333",
        "ws://localhost:3333",
        "ws://[::1]:3333",
        "ws://169.254.169.254:80",
        "ws://0.0.0.0:3333",
        "ws://metadata.google.internal:80",
    ):
        with pytest.raises(ValueError, match="not allowed|must be"):
            record_ws.safe_record_ws_url(bad)


def test_rejects_http_userinfo_query_and_missing_port():
    with pytest.raises(ValueError):
        record_ws.safe_record_ws_url("http://192.168.1.50:3333")
    with pytest.raises(ValueError):
        record_ws.safe_record_ws_url("ws://user:pass@192.168.1.50:3333")
    with pytest.raises(ValueError):
        record_ws.safe_record_ws_url("ws://192.168.1.50:3333?steal=1")
    with pytest.raises(ValueError):
        record_ws.safe_record_ws_url("ws://192.168.1.50")


def test_join_path_token():
    assert (
        record_ws.join_ws_path("ws://192.168.1.50:3333", "secret")
        == "ws://192.168.1.50:3333/secret"
    )
    assert (
        record_ws.join_ws_path("ws://192.168.1.50:3333/secret", "other")
        == "ws://192.168.1.50:3333/secret"
    )


def test_resolve_uses_platform_setting_not_peer():
    url = record_ws.resolve_record_browser_ws(
        configured="ws://192.168.1.50:3333",
        path_token="tok",
        peer_ip="169.254.169.254",
        record_local=True,
    )
    assert url == "ws://192.168.1.50:3333/tok"


def test_resolve_empty_configured_is_server_launch_even_with_peer():
    assert (
        record_ws.resolve_record_browser_ws(
            configured="",
            path_token="tok",
            peer_ip="192.168.1.50",
            record_local=False,
        )
        == ""
    )


def test_record_local_peer_only_rfc1918_fixed_port():
    url = record_ws.resolve_record_browser_ws(
        configured="",
        path_token="tok",
        peer_ip="192.168.1.77",
        record_local=True,
    )
    assert url == "ws://192.168.1.77:3333/tok"
    assert (
        record_ws.resolve_record_browser_ws(
            configured="",
            path_token="tok",
            peer_ip="8.8.8.8",
            record_local=True,
        )
        == ""
    )
    assert (
        record_ws.resolve_record_browser_ws(
            configured="",
            path_token="tok",
            peer_ip="127.0.0.1",
            record_local=True,
        )
        == ""
    )


def test_record_start_body_accepts_dashboard_payload():
    body = RecordStartBody.model_validate(
        {
            "start_url": "https://app.example/",
            "flow_name": "Tour",
            "narrate": True,
            "save_mode": "new",
        }
    )
    assert body.flow_name == "Tour"


def test_record_start_body_rejects_client_ws_fields():
    with pytest.raises(ValidationError):
        RecordStartBody.model_validate(
            {
                "start_url": "https://app.example/",
                "flow_name": "Tour",
                "browser_ws": "ws://169.254.169.254:80",
            }
        )
    with pytest.raises(ValidationError):
        RecordStartBody.model_validate(
            {
                "start_url": "https://app.example/",
                "flow_name": "Tour",
                "record_browser_ws": "ws://10.0.0.1:3333",
            }
        )
