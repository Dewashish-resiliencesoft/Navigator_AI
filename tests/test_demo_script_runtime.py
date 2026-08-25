"""Planning uses demo_script overrides and narration_suggestions."""

from __future__ import annotations

import textwrap

from unittest.mock import MagicMock

from navigator.agent.nodes.planning import _step_narration_hint
from navigator.agent.state import CallDeps
from navigator.core.schemas import WaitFor
from navigator.knowledge.site_graph import parse_site_graph


def _graph_with_meta() -> str:
    return textwrap.dedent(
        """
        version: 1
        site: acme
        base_url: https://acme.example/
        pages:
          home:
            name: Home
            url: /
            selectors:
              body: body
            flows:
              tour:
                - tool: wait_for
                  selector: body
                  timeout_ms: 5000
                  expects:
                    check: visible
                    selector: body
        _meta:
          demo_script:
            full_demo:
              beats:
                - id: flow_tour_0
                  kind: flow_step
                  flow_id: tour
                  step_index: 0
                  spoken: Manual override wins.
                  spoken_source: manual
          narration_suggestions:
            tour:
              - Explore suggestion
        """
    ).strip()


def test_step_narration_hint_manual_override():
    graph = parse_site_graph(_graph_with_meta())
    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=MagicMock(),
    )
    call = WaitFor(
        selector="body",
        timeout_ms=5000,
        expects={"check": "visible", "selector": "body"},
    )
    hint = _step_narration_hint(
        deps, page_id="home", flow_id="tour", step=0, call=call
    )
    assert hint == "Manual override wins."


def test_step_narration_hint_explore_fallback():
    raw = textwrap.dedent(
        """
        version: 1
        site: acme
        base_url: https://acme.example/
        pages:
          home:
            name: Home
            url: /
            selectors:
              body: body
            flows:
              tour:
                - tool: wait_for
                  selector: body
                  timeout_ms: 5000
                  expects:
                    check: visible
                    selector: body
        _meta:
          narration_suggestions:
            tour:
              - From explore
        """
    ).strip()
    graph = parse_site_graph(raw)
    deps = CallDeps(
        graph=graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=MagicMock(),
    )
    call = WaitFor(
        selector="body",
        timeout_ms=5000,
        expects={"check": "visible", "selector": "body"},
    )
    hint = _step_narration_hint(
        deps, page_id="home", flow_id="tour", step=0, call=call
    )
    assert hint == "From explore"
