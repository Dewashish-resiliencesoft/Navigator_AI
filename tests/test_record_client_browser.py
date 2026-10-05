"""Manual record opens in the dashboard browser. It must not launch server Chrome."""

from __future__ import annotations

import pytest

from navigator.client.content import (
    begin_capture,
    ingest_client_record_event,
    start_recorder,
    stop_recorder,
)
from navigator.client.record_proxy import (
    assert_public_product_url,
    rewrite_location,
    upstream_url,
)


def test_client_browser_does_not_launch_server_chrome():
    job = start_recorder(
        start_url="https://example.com/app",
        flow_name="Walkthrough",
        client_browser=True,
        headful=True,
    )
    try:
        assert job.thread is None
        assert job.proc is None
        assert job.client_token
        discarded = ingest_client_record_event(
            job.client_token,
            {
                "tool": "click_element",
                "tag": "button",
                "id": "go",
                "text": "Go",
            },
        )
        assert discarded["discarded"] is True
        begin_capture()
        saved = ingest_client_record_event(
            job.client_token,
            {
                "tool": "click_element",
                "tag": "button",
                "id": "go",
                "text": "Go",
            },
        )
        assert saved["steps"] == 1
        assert job.steps[0].selector == "#go"
    finally:
        stop_recorder()


def test_record_proxy_stays_on_product_host():
    url = upstream_url("https://example.com/app", "login", "next=1")
    assert url == "https://example.com/login?next=1"
    assert rewrite_location(
        "https://example.com/home", "https://example.com/app", "tok"
    ) == "/client/record-surf/tok/home"
    with pytest.raises(ValueError):
        assert_public_product_url("http://127.0.0.1/admin")
    with pytest.raises(ValueError):
        assert_public_product_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError):
        upstream_url("https://example.com/", "../secret", "")
