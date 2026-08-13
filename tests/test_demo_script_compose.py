"""Demo script composer — beat timeline from draft site graph."""

from __future__ import annotations

import textwrap

import yaml

from navigator.knowledge.demo_script import (
    apply_script_patch,
    compose_full_demo_script,
    merge_manual_overrides,
    regenerate_demo_script,
)
from navigator.knowledge.site_graph import parse_site_graph


def _minimal_graph_yaml() -> str:
    return textwrap.dedent(
        """
        version: 1
        site: acme
        base_url: https://acme.example/
        persona:
          product_name: Acme
          one_liner: Demo product
          agent_name: Guide
        demo_playlist:
          - order: 1
            name: Tour
            page_id: home
            flow_id: tour
        pages:
          home:
            name: Home
            url: /
            selectors:
              body: body
              email_field: "#email"
            flows:
              tour:
                - tool: wait_for
                  selector: body
                  timeout_ms: 5000
                  spoken: Welcome to Acme.
                  expects:
                    check: visible
                    selector: body
                - tool: fill_field
                  selector: email_field
                  value: demo@acme.test
                  source: user
                  live_question: What email should I use?
                  spoken: I'll need an email for this part.
                  expects:
                    check: visible
                    selector: email_field
        _meta:
          narration_suggestions:
            tour:
              - Explore line one
              - Explore line two
          semantics:
            tour:
              purpose: Quick product tour
              steps:
                - idx: 0
                  description: Semantics intro
        """
    ).strip()


def test_compose_skips_synthetic_login_when_playlist_has_auth_flow():
    raw = yaml.safe_load(_minimal_graph_yaml())
    raw["demo_playlist"] = [
        {
            "order": 1,
            "name": "Authentication Flow",
            "page_id": "home",
            "flow_id": "authentication_flow",
        }
    ]
    raw["pages"]["home"]["flows"]["authentication_flow"] = raw["pages"]["home"]["flows"][
        "tour"
    ]
    graph = parse_site_graph(yaml.safe_dump(raw))
    script = compose_full_demo_script(graph, intake_enabled=False, include_login=True)
    kinds = [b["kind"] for b in script["beats"]]
    assert "login" not in kinds
    assert any(b.get("flow_id") == "authentication_flow" for b in script["beats"])


def test_compose_skips_auth_flow_when_include_login_off():
    raw = yaml.safe_load(_minimal_graph_yaml())
    raw["demo_playlist"] = [
        {
            "order": 1,
            "name": "Onboarding",
            "page_id": "home",
            "flow_id": "onboarding_flow",
        },
        {
            "order": 2,
            "name": "Tour",
            "page_id": "home",
            "flow_id": "tour",
        },
    ]
    raw["pages"]["home"]["flows"]["onboarding_flow"] = raw["pages"]["home"]["flows"][
        "tour"
    ]
    graph = parse_site_graph(yaml.safe_dump(raw))
    script = compose_full_demo_script(graph, intake_enabled=False, include_login=False)
    ids = [b.get("flow_id") for b in script["beats"]]
    assert "onboarding_flow" not in ids
    assert "tour" in ids



def test_compose_includes_intake_and_wrap():
    graph = parse_site_graph(_minimal_graph_yaml())
    script = compose_full_demo_script(graph, intake_enabled=True)
    kinds = [b["kind"] for b in script["beats"]]
    assert "intake" in kinds
    assert kinds[0] == "intake"
    assert kinds[-1] == "wrap_up"
    assert any(b.get("asks_visitor") for b in script["beats"])


def test_compose_live_input_beat():
    graph = parse_site_graph(_minimal_graph_yaml())
    script = compose_full_demo_script(graph, intake_enabled=False)
    live = [b for b in script["beats"] if b["kind"] == "live_input"]
    assert len(live) == 1
    assert live[0]["field_alias"] == "email_field"
    assert live[0]["asks_visitor"] is True
    assert "email" in live[0]["live_question"].lower()


def test_spoken_priority_yaml_over_explore():
    graph = parse_site_graph(_minimal_graph_yaml())
    script = compose_full_demo_script(graph, intake_enabled=False)
    steps = [b for b in script["beats"] if b["kind"] == "flow_step"]
    assert steps[0]["spoken"] == "Welcome to Acme."
    assert steps[0]["spoken_source"] == "yaml"


def test_spoken_falls_back_to_explore_when_yaml_empty():
    raw = yaml.safe_load(_minimal_graph_yaml())
    raw["pages"]["home"]["flows"]["tour"][0].pop("spoken", None)
    raw["_meta"]["semantics"]["tour"]["steps"] = []
    graph = parse_site_graph(yaml.safe_dump(raw))
    script = compose_full_demo_script(graph, intake_enabled=False)
    steps = [b for b in script["beats"] if b["kind"] == "flow_step"]
    assert steps[0]["spoken"] == "Explore line one"
    assert steps[0]["spoken_source"] == "explore"


def test_spoken_prefers_semantics_over_misaligned_explore():
    raw = yaml.safe_load(_minimal_graph_yaml())
    raw["pages"]["home"]["flows"]["tour"][0].pop("spoken", None)
    raw["pages"]["home"]["flows"]["tour"][1].pop("spoken", None)
    # narration count matches but semantics should win when idx present
    raw["_meta"]["semantics"]["tour"]["steps"] = [
        {"idx": 0, "description": "Opens the home screen."},
        {"idx": 1, "description": "Collects visitor email."},
    ]
    graph = parse_site_graph(yaml.safe_dump(raw))
    script = compose_full_demo_script(graph, intake_enabled=False)
    steps = [b for b in script["beats"] if b["kind"] == "flow_step"]
    assert steps[0]["spoken"] == "Opens the home screen."
    assert steps[0]["spoken_source"] == "semantics"


def test_resolve_flow_step_spoken_fallback_is_pair():
    from navigator.knowledge.demo_script import resolve_flow_step_spoken

    raw = yaml.safe_load(_minimal_graph_yaml())
    raw["pages"]["home"]["flows"]["tour"] = [
        {
            "tool": "click_element",
            "selector": "body",
            "expects": {"check": "visible", "selector": "body"},
        }
    ]
    raw["_meta"] = {}
    graph = parse_site_graph(yaml.safe_dump(raw))
    call = graph.pages["home"].flows["tour"][0]
    spoken, source = resolve_flow_step_spoken(
        graph=graph,
        flow_id="tour",
        step_index=0,
        step_count=1,
        page_id="home",
        page_name="Home",
        call=call,
    )
    assert isinstance(spoken, str)
    assert source == "generated"
    assert spoken  # derived from click action


def test_spoken_action_derived_not_next_step():
    raw = yaml.safe_load(_minimal_graph_yaml())
    raw["pages"]["home"]["flows"]["tour"] = [
        {
            "tool": "click_element",
            "selector": "body",
            "expects": {"check": "visible", "selector": "body"},
        }
    ]
    raw["_meta"] = {}
    graph = parse_site_graph(yaml.safe_dump(raw))
    script = compose_full_demo_script(graph, intake_enabled=False)
    steps = [b for b in script["beats"] if b["kind"] == "flow_step"]
    assert steps[0]["spoken"] == "Opening body."
    assert "Next step." not in steps[0]["spoken"]


def test_misaligned_explore_narration_uses_action_not_wrong_line():
    raw = yaml.safe_load(_minimal_graph_yaml())
    raw["pages"]["home"]["flows"]["tour"] = [
        {
            "tool": "click_element",
            "selector": "body",
            "expects": {"check": "visible", "selector": "body"},
        },
        {
            "tool": "click_element",
            "selector": "email_field",
            "expects": {"check": "visible", "selector": "email_field"},
        },
    ]
    raw["_meta"]["semantics"]["tour"]["steps"] = []
    raw["_meta"]["narration_suggestions"]["tour"] = ["Wrong single line"]
    graph = parse_site_graph(yaml.safe_dump(raw))
    script = compose_full_demo_script(graph, intake_enabled=False)
    steps = [b for b in script["beats"] if b["kind"] == "flow_step"]
    assert len(steps) == 2
    assert steps[1]["spoken_source"] == "generated"
    assert "Wrong single line" not in steps[1]["spoken"]


def test_merge_manual_overrides():
    graph = parse_site_graph(_minimal_graph_yaml())
    composed = compose_full_demo_script(graph, intake_enabled=False)
    stored = {
        "full_demo": {
            "beats": [
                {
                    "id": "flow_tour_0",
                    "kind": "flow_step",
                    "spoken": "Client edited line.",
                    "spoken_source": "manual",
                }
            ]
        }
    }
    merged = merge_manual_overrides(composed, stored)
    step0 = next(b for b in merged["beats"] if b["id"] == "flow_tour_0")
    assert step0["spoken"] == "Client edited line."
    assert step0["spoken_source"] == "manual"


def test_apply_script_patch_syncs_spoken_to_yaml():
    yaml_text = _minimal_graph_yaml()
    graph = parse_site_graph(yaml_text)
    script = compose_full_demo_script(graph, intake_enabled=False)
    beats = script["beats"]
    for b in beats:
        if b["id"] == "flow_tour_0":
            b["spoken"] = "Synced to YAML."
            b["spoken_source"] = "manual"
    new_yaml = apply_script_patch(yaml_text, beats=beats)
    raw = yaml.safe_load(new_yaml)
    spoken = raw["pages"]["home"]["flows"]["tour"][0]["spoken"]
    assert spoken == "Synced to YAML."
    assert "_meta" in raw and "demo_script" in raw["_meta"]


def test_regenerate_preserves_manual():
    yaml_text = _minimal_graph_yaml()
    graph = parse_site_graph(yaml_text)
    stored = {
        "full_demo": {
            "beats": [
                {
                    "id": "flow_tour_0",
                    "kind": "flow_step",
                    "flow_id": "tour",
                    "step_index": 0,
                    "spoken": "Keep this.",
                    "spoken_source": "manual",
                }
            ]
        }
    }
    regen = regenerate_demo_script(graph, stored_script=stored, intake_enabled=False)
    step0 = next(b for b in regen["beats"] if b["id"] == "flow_tour_0")
    assert step0["spoken"] == "Keep this."
    assert step0["spoken_source"] == "manual"
