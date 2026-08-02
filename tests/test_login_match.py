"""login_match: one definition for recorder, save-gate, and session expiry."""

from __future__ import annotations

import pytest

from navigator.automation.login_match import (
    LoginConfig,
    VAULT_PASSWORD_SENTINEL,
    assert_no_login_in_graph,
    is_password_field,
    looks_like_login,
    looks_like_permission_denied,
    same_page_path,
)
from navigator.knowledge.site_graph import SiteGraphError, parse_site_graph


def test_password_field_by_type_and_autocomplete():
    assert is_password_field({"type": "password"})
    assert is_password_field({"autocomplete": "current-password"})
    assert not is_password_field({"type": "email"})


def test_looks_like_login_url_and_selector():
    cfg = LoginConfig(login_url="https://acme.example/login")
    assert looks_like_login(config=cfg, url="https://acme.example/login/") 
    assert looks_like_login(config=cfg, selector="#password")
    assert looks_like_login(config=cfg, element={"type": "password"})
    assert looks_like_login(config=cfg, url="https://acme.example/inbox") is None
    assert looks_like_login(config=cfg, selector="invite_email") is None


def test_permission_denied_not_login():
    assert looks_like_permission_denied(page_text="Access denied")
    assert looks_like_permission_denied(url="https://x/403")
    assert not looks_like_permission_denied(page_text="Welcome back", url="/login")


def test_same_page_path():
    assert same_page_path("https://a/x", "https://b/x/")
    assert not same_page_path("https://a/x", "https://a/y")


_GRAPH = """
version: 1
site: acme
base_url: https://acme.example/
pages:
  main:
    name: Main
    url: /
    selectors:
      send: "#send"
      password: "#password"
    flows:
      default_walkthrough:
        - tool: click_element
          selector: send
          expects: {check: visible, selector: send, timeout_ms: 1000}
      topic_search:
        - tool: fill_field
          selector: password
          value: "{sentinel}"
          expects: {check: visible, selector: password, timeout_ms: 1000}
demo_playlist:
  - order: 1
    name: Default
    page_id: main
    flow_id: default_walkthrough
""".replace("{sentinel}", VAULT_PASSWORD_SENTINEL)


def test_assert_rejects_topic_login_even_with_toggle():
    graph = parse_site_graph(_GRAPH)
    cfg = LoginConfig(login_url="https://acme.example/login")
    with pytest.raises(SiteGraphError, match="Topic flow"):
        assert_no_login_in_graph(
            graph, cfg, include_login_in_default_flow=True
        )


def test_assert_allows_default_with_toggle():
    yaml = _GRAPH.replace("topic_search", "default_walkthrough", 1)
    # Make the password step the default flow instead.
    yaml = """
version: 1
site: acme
base_url: https://acme.example/
pages:
  main:
    name: Main
    url: /login
    selectors:
      password: "#password"
    flows:
      default_walkthrough:
        - tool: fill_field
          selector: password
          value: "__NAV_VAULT_PASSWORD__"
          expects: {check: visible, selector: password, timeout_ms: 1000}
demo_playlist:
  - order: 1
    name: Default
    page_id: main
    flow_id: default_walkthrough
"""
    graph = parse_site_graph(yaml)
    cfg = LoginConfig(login_url="https://acme.example/login")
    assert_no_login_in_graph(graph, cfg, include_login_in_default_flow=True)
    with pytest.raises(SiteGraphError, match="Default flow"):
        assert_no_login_in_graph(graph, cfg, include_login_in_default_flow=False)
