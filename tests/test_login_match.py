"""login_match: one definition for recorder, save-gate, and session expiry."""

from __future__ import annotations

import pytest

from navigator.automation.login_match import (
    LoginConfig,
    VAULT_PASSWORD_SENTINEL,
    assert_no_login_in_graph,
    demo_playlist_for_toggle,
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


def test_assert_rejects_topic_login():
    graph = parse_site_graph(_GRAPH)
    cfg = LoginConfig(login_url="https://acme.example/login")
    with pytest.raises(SiteGraphError, match="Topic flow"):
        assert_no_login_in_graph(graph, cfg)


def test_assert_allows_playlist_login_rejects_same_flow_off_playlist():
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
    name: Walkthrough
    page_id: main
    flow_id: default_walkthrough
"""
    graph = parse_site_graph(yaml)
    cfg = LoginConfig(login_url="https://acme.example/login")
    assert_no_login_in_graph(graph, cfg)
    yaml_no_playlist = yaml.replace(
        "demo_playlist:\n  - order: 1\n    name: Walkthrough\n    page_id: main\n    flow_id: default_walkthrough\n",
        "",
    )
    graph_no_playlist = parse_site_graph(yaml_no_playlist)
    with pytest.raises(SiteGraphError, match="Topic flow"):
        assert_no_login_in_graph(graph_no_playlist, cfg)


def test_assert_allows_recorded_authentication_flow_without_toggle():
    yaml = """
version: 1
site: acme
base_url: https://acme.example/
pages:
  dashboard:
    name: Dashboard
    url: /
    selectors:
      already_have_an_account_sign_in: "text=Sign in"
    flows:
      authentication_flow:
        - tool: click_element
          selector: already_have_an_account_sign_in
          expects: {check: visible, selector: already_have_an_account_sign_in, timeout_ms: 5000}
demo_playlist:
  - order: 1
    name: Authentication Flow
    page_id: dashboard
    flow_id: authentication_flow
"""
    graph = parse_site_graph(yaml)
    cfg = LoginConfig(login_url="https://acme.example/login")
    assert_no_login_in_graph(graph, cfg)


def test_assert_allows_onboarding_flow_sign_in_without_toggle():
    yaml = """
version: 1
site: acme
base_url: https://acme.example/
pages:
  dashboard:
    name: Dashboard
    url: /
    selectors:
      sign_in: "text=Sign in"
    flows:
      onboarding_flow:
        - tool: click_element
          selector: sign_in
          expects: {check: visible, selector: sign_in, timeout_ms: 5000}
demo_playlist:
  - order: 1
    name: Onboarding
    page_id: dashboard
    flow_id: onboarding_flow
"""
    graph = parse_site_graph(yaml)
    cfg = LoginConfig(login_url="https://acme.example/login")
    assert_no_login_in_graph(graph, cfg)


def test_assert_allows_sign_in_when_flow_explicitly_allowed():
    yaml = """
version: 1
site: acme
base_url: https://acme.example/
pages:
  dashboard:
    name: Dashboard
    url: /
    selectors:
      sign_in: "text=Sign in"
    flows:
      onboarding_flow:
        - tool: click_element
          selector: sign_in
          expects: {check: visible, selector: sign_in, timeout_ms: 5000}
demo_playlist: []
"""
    graph = parse_site_graph(yaml)
    cfg = LoginConfig(login_url="https://acme.example/login")
    assert_no_login_in_graph(
        graph,
        cfg,
        allow_flows=frozenset({("dashboard", "onboarding_flow")}),
    )
    yaml = """
version: 1
site: acme
base_url: https://acme.example/
pages:
  dashboard:
    name: Dashboard
    url: /
    selectors:
      sign_in: "text=Sign in"
    flows:
      getting_started:
        - tool: click_element
          selector: sign_in
          expects: {check: visible, selector: sign_in, timeout_ms: 5000}
demo_playlist:
  - order: 1
    name: Getting started
    page_id: dashboard
    flow_id: getting_started
"""
    graph = parse_site_graph(yaml)
    cfg = LoginConfig(login_url="https://acme.example/login")
    assert_no_login_in_graph(graph, cfg)


_TOGGLE_PLAYLIST = """
version: 1
site: acme
base_url: https://acme.example/
pages:
  dashboard:
    name: Dashboard
    url: /
    selectors:
      send: "#send"
    flows:
      onboarding_flow:
        - tool: click_element
          selector: send
          expects: {check: visible, selector: send, timeout_ms: 1000}
      send_campaign:
        - tool: click_element
          selector: send
          expects: {check: visible, selector: send, timeout_ms: 1000}
demo_playlist:
  - order: 1
    name: onboarding flow
    page_id: dashboard
    flow_id: onboarding_flow
  - order: 2
    name: send campaign
    page_id: dashboard
    flow_id: send_campaign
"""


def test_demo_playlist_for_toggle_off_drops_login_keeps_topic():
    graph = parse_site_graph(_TOGGLE_PLAYLIST)
    off = demo_playlist_for_toggle(graph, include_login=False)
    assert [i.flow_id for i in off] == ["send_campaign"]
    assert off[0].order == 1


def test_demo_playlist_for_toggle_on_keeps_login_first():
    graph = parse_site_graph(_TOGGLE_PLAYLIST)
    on = demo_playlist_for_toggle(graph, include_login=True)
    assert [i.flow_id for i in on] == ["onboarding_flow", "send_campaign"]

