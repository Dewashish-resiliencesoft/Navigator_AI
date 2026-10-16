"""Explore topology → draft site-graph walkthrough."""

from __future__ import annotations

import yaml

from navigator.client.content import (
    draft_needs_explore_promote,
    promote_topology_to_demo_yaml,
    resolve_topology_yaml,
)
from navigator.knowledge.site_graph import parse_site_graph
from navigator.knowledge.topology import save_topology


_STUB = """\
version: 1
site: client
base_url: https://resiliohub.com/
persona:
  product_name: Resilience
  one_liner: ""
  agent_name: Navigator AI
  tone: friendly
demo_playlist:
  - order: 1
    name: Default walkthrough
    page_id: home
    flow_id: default_walkthrough
pages:
  home:
    name: Home
    url: /
    selectors:
      body: body
    flows:
      default_walkthrough:
        - tool: wait_for
          selector: body
          timeout_ms: 15000
          expects: {check: visible, selector: body}
"""

_TOPO = {
    "site": "resiliohub",
    "base_url": "https://resiliohub.com/",
    "pages": {
        "dash": {
            "url": "https://resiliohub.com/dashboard/",
            "title": "Dashboard | ResilioHub",
            "selectors": {"u": "#radix"},
            "flows": {},
        },
        "login": {
            "url": "https://resiliohub.com/login/",
            "title": "Login",
            "selectors": {},
            "flows": {},
        },
        "inbox": {
            "url": "https://resiliohub.com/inbox/",
            "title": "Inbox",
            "selectors": {},
            "flows": {},
        },
        "phone": {
            "url": "https://resiliohub.com/phonebook/",
            "title": "Phonebook",
            "selectors": {},
            "flows": {},
        },
    },
    "demo_playlist": [],
    "_meta": {"non_demo": True},
}


def test_promote_builds_multi_page_walkthrough():
    out = promote_topology_to_demo_yaml(
        _STUB,
        yaml.safe_dump(_TOPO),
        bio_fields={
            "company_name": "Resiliencesoft",
            "usp": "All-in-one WhatsApp Business API and AI CRM",
            "key_features": "WhatsApp shared inbox, Instagram automation",
        },
    )
    graph = parse_site_graph(out)
    assert "login" not in graph.pages
    assert "dashboard" in graph.pages
    assert "inbox" in graph.pages
    assert len(graph.pages) == 3
    assert graph.demo_playlist
    assert graph.effective_persona().product_name == "Resiliencesoft"
    page_id, flow_id = graph.primary_flow()  # type: ignore[misc]
    steps = graph.flow(page_id, flow_id)
    assert len(steps) >= 3  # wait + navigate + wait at least
    tools = [s.tool for s in steps]
    assert "navigate" in tools
    spoken0 = (getattr(steps[0], "spoken", None) or "")
    assert "WhatsApp" in spoken0 or "Resiliencesoft" in spoken0
    # explore radix selectors discarded
    for page in graph.pages.values():
        assert page.selectors == {"body": "body"}
    assert graph.pages["inbox"].name == "Inbox"


def test_draft_needs_promote_stub_vs_rich():
    assert draft_needs_explore_promote(_STUB) is True
    rich = promote_topology_to_demo_yaml(_STUB, yaml.safe_dump(_TOPO))
    # after promote, meta.source marks it refreshable
    assert draft_needs_explore_promote(rich) is True


def test_resolve_topology_by_host_slug(tmp_path, monkeypatch):
    monkeypatch.setattr("navigator.knowledge.topology._ROOT", tmp_path)
    save_topology("resiliohub", yaml.safe_dump(_TOPO), page_count=4)
    found = resolve_topology_yaml("resilience", _STUB)
    assert "dashboard" in found
    assert "inbox" in found
