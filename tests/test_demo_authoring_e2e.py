"""End-to-end API checks for demo authoring (scope, approvals, append, script)."""

from __future__ import annotations

import textwrap

import yaml

from navigator.automation.record import RecordedStep
from navigator.client.content import merge_recorded_flow
from test_api import ACME, register
from test_client_dashboard import _cleanup, _client


def _headers(client, auth_store, product_id: str) -> dict:
    auth_store.create_user(
        product_id=product_id, email="author@acme.com", password="password"
    )
    r = client.post(
        "/v1/auth/login",
        json={"email": "author@acme.com", "password": "password"},
        headers={"Host": "localhost"},
    )
    assert r.status_code == 200, r.text
    return {"Host": "localhost", "Authorization": f"Bearer {r.json()['access_token']}"}


def _graph_with_pending() -> str:
    return textwrap.dedent(
        """
        version: 1
        site: acme
        base_url: https://acme.example/
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
              btn_save: button.save
            flows:
              tour:
                - tool: click_element
                  selector: body
                  expects: {check: visible, selector: body}
                - tool: click_element
                  selector: btn_save
                  expects: {check: visible, selector: btn_save}
        _meta:
          pending_approvals:
            tour:
              - idx: 1
                alias: btn_save
                selector: button.save
                reason: submit
                approved: false
          narration_suggestions:
            tour: ["Open the dashboard.", "Save the record."]
          step_timing:
            tour:
              - idx: 0
                speak_ms: 2400
              - idx: 1
                speak_ms: 1800
        """
    ).strip()


def test_explore_start_retired_returns_410(tmp_path):
    bundle = _client(tmp_path, client_api_key="nav_test")
    client, prev, registry, log, auth_store = bundle
    try:
        p = register(client, "Acme Inbox", ACME)
        headers = _headers(client, auth_store, p["id"])
        r = client.post(
            "/client/api/explore/start",
            json={
                "include_paths": ["/contacts"],
                "exclude_paths": ["/settings"],
                "exclude_labels": ["logout"],
                "save_mode": "new",
                "new_flow_name": "scoped",
            },
            headers=headers,
        )
        # Phase 2: demo Auto-Explore write routes retired (Product Explore is Phase 3).
        assert r.status_code == 410, r.text
        assert "auto-explore" in r.json()["detail"].lower()
        stop = client.post("/client/api/explore/stop", headers=headers)
        assert stop.status_code == 410, stop.text
    finally:
        _cleanup(bundle, prev)


def test_demo_script_surfaces_recorded_timing_and_approval(tmp_path):
    bundle = _client(tmp_path, client_api_key="nav_test")
    client, prev, registry, log, auth_store = bundle
    try:
        p = register(client, "Acme Inbox", ACME)
        headers = _headers(client, auth_store, p["id"])
        registry.put_site_graph(p["id"], _graph_with_pending(), "yaml", publish=False)
        r = client.get("/client/api/site-graph/demo-script", headers=headers)
        assert r.status_code == 200, r.text
        beats = r.json()["beats"]
        save = next(b for b in beats if b.get("step_index") == 1 and b.get("flow_id") == "tour")
        assert save["kind"] == "pending_approval"
        assert save.get("needs_approval") is True
        assert save.get("speak_ms") == 1800
        assert save.get("spoken_source") in {"recorded", "semantics", "explore", "yaml"}
    finally:
        _cleanup(bundle, prev)


def test_merge_append_preserves_prior_steps(tmp_path):
    base = textwrap.dedent(
        """
        version: 1
        site: acme
        base_url: https://acme.example/
        demo_playlist: []
        pages:
          dashboard:
            name: Dashboard
            url: /
            selectors: {body: body, a: '#a', b: '#b', c: '#c', d: '#d', e: '#e'}
            flows:
              tour:
                - tool: wait_for
                  selector: body
                  timeout_ms: 1000
                  expects: {check: visible, selector: body}
        """
    ).strip()
    once = merge_recorded_flow(
        base,
        flow_name="Tour",
        flow_id="tour",
        page_id="dashboard",
        steps=[
            RecordedStep(tool="click_element", alias="a", selector="#a"),
            RecordedStep(tool="click_element", alias="b", selector="#b"),
            RecordedStep(tool="click_element", alias="c", selector="#c"),
        ],
        product_name="Acme",
        base_url="https://acme.example/",
    )
    twice = merge_recorded_flow(
        once,
        flow_name="Tour",
        flow_id="tour",
        page_id="dashboard",
        steps=[
            RecordedStep(tool="click_element", alias="d", selector="#d"),
            RecordedStep(tool="click_element", alias="e", selector="#e"),
        ],
        product_name="Acme",
        base_url="https://acme.example/",
        update_existing=True,
    )
    doc = yaml.safe_load(twice)
    steps = doc["pages"]["dashboard"]["flows"]["tour"]
    assert len(steps) == 5
    assert len(doc["demo_playlist"]) == 1


def test_merge_replace_overwrites_prior_steps(tmp_path):
    base = textwrap.dedent(
        """
        version: 1
        site: acme
        base_url: https://acme.example/
        demo_playlist:
          - order: 1
            name: Tour
            page_id: dashboard
            flow_id: tour
        pages:
          dashboard:
            name: Dashboard
            url: /
            selectors: {body: body, a: '#a', b: '#b', c: '#c'}
            flows:
              tour:
                - tool: click_element
                  selector: a
                  expects: {check: visible, selector: a}
                - tool: click_element
                  selector: b
                  expects: {check: visible, selector: b}
        """
    ).strip()
    replaced = merge_recorded_flow(
        base,
        flow_name="Tour",
        flow_id="tour",
        page_id="dashboard",
        steps=[RecordedStep(tool="click_element", alias="c", selector="#c")],
        product_name="Acme",
        base_url="https://acme.example/",
        update_existing=True,
        replace_steps=True,
    )
    doc = yaml.safe_load(replaced)
    steps = doc["pages"]["dashboard"]["flows"]["tour"]
    assert len(steps) == 1
    assert steps[0]["selector"] == "c"
    assert len(doc["demo_playlist"]) == 1
    assert doc["demo_playlist"][0]["flow_id"] == "tour"


def test_merge_replace_dedupes_playlist_and_scrubs_ghost_page():
    from navigator.client.content import merge_recorded_flow, resolve_flow_page_id

    base = (
        "version: 1\nsite: acme\nbase_url: https://acme.example/\n"
        "demo_playlist:\n"
        "  - order: 1\n    name: Tour\n    page_id: explore\n    flow_id: tour\n"
        "  - order: 2\n    name: Tour copy\n    page_id: dashboard\n    flow_id: tour\n"
        "pages:\n  explore:\n    name: Explore\n    url: /\n"
        "    selectors:\n      body: body\n      old: '#old'\n"
        "    flows:\n      tour:\n        - tool: click_element\n"
        "          selector: old\n          expects: {check: visible, selector: old}\n"
        "  dashboard:\n    name: Dashboard\n    url: /\n"
        "    selectors:\n      body: body\n      old: '#old'\n"
        "    flows:\n      tour:\n        - tool: click_element\n"
        "          selector: old\n          expects: {check: visible, selector: old}\n"
    )
    assert resolve_flow_page_id(base, "tour") == "explore"
    merged = merge_recorded_flow(
        base,
        flow_name="Tour",
        flow_id="tour",
        page_id="explore",
        steps=[RecordedStep(tool="click_element", alias="new", selector="#new")],
        product_name="Acme",
        base_url="https://acme.example/",
        update_existing=True,
        replace_steps=True,
    )
    doc = yaml.safe_load(merged)
    assert len(doc["demo_playlist"]) == 1
    assert doc["demo_playlist"][0]["page_id"] == "explore"
    assert "tour" not in doc["pages"]["dashboard"]["flows"]
    assert len(doc["pages"]["explore"]["flows"]["tour"]) == 1
    assert doc["pages"]["explore"]["flows"]["tour"][0]["selector"] == "new"
