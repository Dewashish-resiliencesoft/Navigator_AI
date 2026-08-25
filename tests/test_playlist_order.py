"""Playlist order: reordering rows must renumber and stick."""

from __future__ import annotations

from navigator.client.content import (
    apply_playlist_to_yaml,
    clear_all_flows_from_yaml,
    playlist_from_graph,
    remove_flow_from_yaml,
    reset_site_graph_for_explore,
)
from navigator.knowledge.site_graph import parse_site_graph

_GRAPH = """
version: 1
site: acme
base_url: https://example.com/
persona:
  product_name: Acme
  one_liner: test
pages:
  main:
    name: Main
    url: /
    selectors:
      body: body
    flows:
      first:
        - tool: navigate
          page_id: main
          expects: {check: visible, selector: body}
      second:
        - tool: navigate
          page_id: main
          expects: {check: visible, selector: body}
demo_playlist:
  - order: 1
    name: First
    page_id: main
    flow_id: first
  - order: 2
    name: Second
    page_id: main
    flow_id: second
"""


def test_reorder_playlist_swaps_primary_flow():
    graph = parse_site_graph(_GRAPH)
    assert graph.primary_flow() == ("main", "first")

    swapped = [
        {"order": 1, "name": "Second", "page_id": "main", "flow_id": "second"},
        {"order": 2, "name": "First", "page_id": "main", "flow_id": "first"},
    ]
    yaml_out = apply_playlist_to_yaml(_GRAPH, swapped)
    updated = parse_site_graph(yaml_out)
    assert updated.primary_flow() == ("main", "second")
    pl = playlist_from_graph(updated)
    assert [p["flow_id"] for p in pl] == ["second", "first"]


def test_remove_flow_drops_playlist_and_definition():
    out = remove_flow_from_yaml(_GRAPH, flow_id="first", page_id="main")
    g = parse_site_graph(out)
    assert [p.flow_id for p in g.demo_playlist] == ["second"]
    assert "first" not in g.pages["main"].flows
    assert "second" in g.pages["main"].flows


def test_clear_all_flows_wipes_playlist_steps_and_meta():
    graph_with_meta = _GRAPH + """
_meta:
  semantics:
    first:
      purpose: Tour intro
  demo_script:
    full_demo:
      beats: []
"""
    out = clear_all_flows_from_yaml(graph_with_meta)
    g = parse_site_graph(out)
    assert g.demo_playlist == ()
    assert g.pages["main"].flows == {}
    assert g.meta.get("semantics") is None
    assert g.meta.get("demo_script") is None
    assert playlist_from_graph(g) == []


def test_reset_site_graph_for_explore_minimal_shell():
    out = reset_site_graph_for_explore(_GRAPH)
    g = parse_site_graph(out)
    assert g.demo_playlist == ()
    assert set(g.pages.keys()) == {"home"}
    assert g.pages["home"].flows == {}
    assert g.base_url == "https://example.com/"
    assert g.persona.product_name == "Acme"
    assert g.meta == {}
