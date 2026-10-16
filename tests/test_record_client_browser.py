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
    _inject_html,
    assert_public_product_url,
    rewrite_location,
    rewrite_surf_url,
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


def test_record_proxy_rewrites_root_assets_through_surf():
    assert rewrite_surf_url("/static/app.css", "tok", "example.com") == (
        "/client/record-surf/tok/static/app.css"
    )
    assert rewrite_surf_url("assets/app.css", "tok", "example.com") == "assets/app.css"
    assert rewrite_surf_url("https://cdn.example/a.png", "tok", "example.com") == (
        "https://cdn.example/a.png"
    )
    assert rewrite_surf_url("https://example.com/pricing?x=1", "tok", "example.com") == (
        "/client/record-surf/tok/pricing?x=1"
    )
    html = (
        "<!doctype html><html><head>"
        '<link rel="stylesheet" href="/static/app.css">'
        '<script src="/static/app.js"></script>'
        "</head><body>"
        '<img src="https://cdn.example/a.png" srcset="/img/a.png 1x, /img/b.png 2x">'
        '<a href="https://example.com/pricing">Go</a>'
        "</body></html>"
    )
    out = _inject_html(html.encode(), "tok", "example.com").decode()
    assert 'href="/client/record-surf/tok/static/app.css"' in out
    assert 'src="/client/record-surf/tok/static/app.js"' in out
    assert 'src="https://cdn.example/a.png"' in out
    assert "/client/record-surf/tok/img/a.png 1x" in out
    assert 'href="/client/record-surf/tok/pricing"' in out
    assert "HTMLLinkElement" in out
