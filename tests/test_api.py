"""The wrapper API, including the part that matters most: tenant isolation.

Two different products are registered and demoed against two different HTML
fixtures in the same test run. Neither may see the other's site graph, demos, or
action log.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from navigator.app import main as app_module
from navigator.app.registry import Registry
from navigator.app.runner import DemoRunner
from navigator.logs.store import ActionLog

FIXTURES = Path(__file__).parent / "fixtures"


def graph_yaml(site: str, product_name: str, fixture: str, button: str) -> str:
    """A minimal but real site graph pointed at a local fixture."""
    return textwrap.dedent(
        f"""
        version: 1
        site: {site}
        base_url: {FIXTURES.as_uri()}/
        persona:
          product_name: {product_name}
          one_liner: a test product
        pages:
          main:
            name: Main
            url: {fixture}
            selectors:
              trigger: "{button}"
              result: ".message.sent"
              input: "#message-input"
            flows:
              happy_path:
                - tool: navigate
                  page_id: main
                  expects: {{check: visible, selector: trigger}}
                - tool: fill_field
                  selector: input
                  value: "hello from {site}"
                  expects:
                    check: value_equals
                    selector: input
                    expected: "hello from {site}"
                - tool: click_element
                  selector: trigger
                  expects:
                    check: text_contains
                    selector: result
                    expected: "hello from {site}"
              broken_path:
                - tool: navigate
                  page_id: main
                  expects: {{check: visible, selector: trigger}}
                - tool: click_element
                  selector: result
                  expects: {{check: visible, selector: result, timeout_ms: 600}}
        """
    )


ACME = graph_yaml("acme-inbox", "Acme Inbox", "crm_dashboard.html", "#send-btn")
GLOBEX = graph_yaml("globex-desk", "Globex Desk", "crm_dashboard.html", "#send-btn")


@pytest.fixture
def client(tmp_path):
    """A fresh app with isolated registry, log, and runner."""
    registry = Registry(tmp_path / "registry.db")
    log = ActionLog(tmp_path / "actions.db")
    runner = DemoRunner(
        str(tmp_path / "actions.db"), headful=False, archive_dir=tmp_path / "archives"
    )

    app_module.app.dependency_overrides[app_module.get_registry] = lambda: registry
    app_module.app.dependency_overrides[app_module.get_log] = lambda: log
    app_module.app.dependency_overrides[app_module.get_runner] = lambda: runner

    with TestClient(app_module.app) as c:
        c.runner = runner  # tests need to wait on demo threads
        yield c

    app_module.app.dependency_overrides.clear()
    registry.close()
    log.close()


def register(client, name: str, yaml_text: str | None = None) -> dict:
    r = client.post("/v1/products", json={"name": name})
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Token {body['api_key']}"}
    if yaml_text:
        up = client.put(
            "/v1/products/site-graph",
            json={"yaml": yaml_text, "publish": True},
            headers=headers,
        )
        assert up.status_code == 201, up.text
    return {"id": body["product"]["product_id"], "headers": headers}


def run_demo(client, product, page="main", flow="happy_path") -> dict:
    r = client.post(
        "/v1/demos",
        json={"page_id": page, "flow_id": flow},
        headers=product["headers"],
    )
    assert r.status_code == 202, r.text
    demo_id = r.json()["demo_id"]
    client.runner.wait(__import__("uuid").UUID(demo_id), timeout=90)
    final = client.get(f"/v1/demos/{demo_id}", headers=product["headers"])
    assert final.status_code == 200, final.text
    return final.json()


# -- registration & auth ------------------------------------------------------


def test_register_returns_a_key_and_a_slug(client):
    r = client.post("/v1/products", json={"name": "Acme Inbox"})
    assert r.status_code == 201
    assert r.json()["product"]["product_id"] == "acme-inbox"
    assert r.json()["api_key"].startswith("nav_")


def test_duplicate_registration_conflicts(client):
    client.post("/v1/products", json={"name": "Acme"})
    assert client.post("/v1/products", json={"name": "Acme"}).status_code == 409


def test_routes_require_a_key(client):
    assert client.get("/v1/products/me").status_code == 401
    assert (
        client.get(
            "/v1/products/me", headers={"Authorization": "Token nav_bogus"}
        ).status_code
        == 401
    )


def test_malformed_auth_header_rejected(client):
    r = client.get("/v1/products/me", headers={"Authorization": "Bearer xyz"})
    assert r.status_code == 401


def test_whoami_identifies_the_caller(client):
    p = register(client, "Acme Inbox")
    r = client.get("/v1/products/me", headers=p["headers"])
    assert r.json()["product_id"] == "acme-inbox"


# -- site graph upload --------------------------------------------------------


def test_upload_then_read_back(client):
    p = register(client, "Acme Inbox", ACME)
    r = client.get("/v1/products/site-graph", headers=p["headers"])
    assert r.status_code == 200
    assert r.json()["revision"] == 1
    assert r.json()["site"] == "acme-inbox"


def test_invalid_upload_returns_422_with_the_loader_message(client):
    p = register(client, "Acme Inbox")
    broken = ACME.replace('selector: input', 'selector: ghost')
    r = client.put(
        "/v1/products/site-graph", json={"yaml": broken}, headers=p["headers"]
    )
    assert r.status_code == 422
    assert "unknown selector 'ghost'" in r.json()["detail"]


def test_rejected_upload_leaves_the_active_revision_alone(client):
    p = register(client, "Acme Inbox", ACME)
    client.put(
        "/v1/products/site-graph",
        json={"yaml": ACME.replace("selector: input", "selector: ghost")},
        headers=p["headers"],
    )
    r = client.get("/v1/products/site-graph", headers=p["headers"])
    assert r.json()["revision"] == 1, "a bad push must not break a live demo"


def test_uploads_are_versioned_and_rollback_works(client):
    p = register(client, "Acme Inbox", ACME)
    client.put(
        "/v1/products/site-graph",
        json={
            "yaml": ACME.replace("version: 1", "version: 2"),
            "source": "sdk",
            "publish": True,
        },
        headers=p["headers"],
    )
    assert len(client.get("/v1/products/site-graph/revisions", headers=p["headers"]).json()) == 2
    assert client.get("/v1/products/site-graph", headers=p["headers"]).json()["source"] == "sdk"

    client.post(
        "/v1/products/site-graph/activate", json={"revision": 1}, headers=p["headers"]
    )
    active = client.get("/v1/products/site-graph", headers=p["headers"]).json()
    assert active["revision"] == 1
    assert active["graph_version"] == 1


def test_relative_base_url_is_rejected_on_upload(client):
    p = register(client, "Acme Inbox")
    r = client.put(
        "/v1/products/site-graph",
        json={"yaml": ACME.replace(f"{FIXTURES.as_uri()}/", "../../etc/")},
        headers=p["headers"],
    )
    assert r.status_code == 422
    assert "must be absolute" in r.json()["detail"]


def test_flows_endpoint_lists_what_the_customer_authored(client):
    p = register(client, "Acme Inbox", ACME)
    r = client.get("/v1/products/flows", headers=p["headers"])
    assert r.json() == {"main": ["broken_path", "happy_path"]}


def test_demo_before_upload_is_404(client):
    p = register(client, "Acme Inbox")
    r = client.post(
        "/v1/demos", json={"page_id": "main", "flow_id": "happy_path"}, headers=p["headers"]
    )
    assert r.status_code == 404


def test_unknown_flow_is_422(client):
    p = register(client, "Acme Inbox", ACME)
    r = client.post(
        "/v1/demos", json={"page_id": "main", "flow_id": "nope"}, headers=p["headers"]
    )
    assert r.status_code == 422
    assert "no flow 'nope'" in r.json()["detail"]


# -- running demos ------------------------------------------------------------


def test_demo_runs_to_completion_and_logs_actions(client):
    p = register(client, "Acme Inbox", ACME)
    demo = run_demo(client, p)

    assert demo["status"] == "finished", demo.get("error")
    assert demo["actions"] == 3
    assert demo["failures"] == 0

    actions = client.get(
        f"/v1/demos/{demo['demo_id']}/actions", headers=p["headers"]
    ).json()
    assert [a["tool_call"]["tool"] for a in actions] == [
        "navigate",
        "fill_field",
        "click_element",
    ]
    assert all(a["verify"]["passed"] for a in actions)
    assert all(a["product_id"] == "acme-inbox" for a in actions)


def test_demo_narration_uses_the_products_own_persona(client):
    p = register(client, "Acme Inbox", ACME)
    demo = run_demo(client, p)
    intro = demo["said"][0]
    assert "Acme Inbox" in intro
    assert "WhatsApp" not in intro, "no product-specific text may leak from code"


def test_failing_flow_is_recorded_not_crashed(client):
    p = register(client, "Acme Inbox", ACME)
    demo = run_demo(client, p, flow="broken_path")

    assert demo["status"] == "finished", "a failed postcondition is data, not a crash"
    assert demo["failures"] == 1

    failures = client.get("/v1/products/failures", headers=p["headers"]).json()
    assert len(failures) == 1
    assert failures[0]["product_id"] == "acme-inbox"
    assert failures[0]["verify"]["passed"] is False


# -- tenant isolation, the point of the whole layer ---------------------------


def test_two_products_demo_concurrently_without_crosstalk(client):
    acme = register(client, "Acme Inbox", ACME)
    globex = register(client, "Globex Desk", GLOBEX)

    a = run_demo(client, acme)
    g = run_demo(client, globex)

    assert a["status"] == "finished" and g["status"] == "finished"
    assert a["product_id"] == "acme-inbox"
    assert g["product_id"] == "globex-desk"

    a_actions = client.get(f"/v1/demos/{a['demo_id']}/actions", headers=acme["headers"]).json()
    g_actions = client.get(f"/v1/demos/{g['demo_id']}/actions", headers=globex["headers"]).json()

    assert {x["product_id"] for x in a_actions} == {"acme-inbox"}
    assert {x["product_id"] for x in g_actions} == {"globex-desk"}
    # Each typed its own value into its own browser context.
    assert any("hello from acme-inbox" in str(x["tool_call"]) for x in a_actions)
    assert not any("globex" in str(x["tool_call"]) for x in a_actions)


def test_one_product_cannot_read_anothers_demo(client):
    acme = register(client, "Acme Inbox", ACME)
    globex = register(client, "Globex Desk", GLOBEX)
    a = run_demo(client, acme)

    assert client.get(f"/v1/demos/{a['demo_id']}", headers=globex["headers"]).status_code == 404
    assert (
        client.get(f"/v1/demos/{a['demo_id']}/actions", headers=globex["headers"]).status_code
        == 404
    )


def test_one_product_cannot_read_anothers_site_graph(client):
    acme = register(client, "Acme Inbox", ACME)
    globex = register(client, "Globex Desk", GLOBEX)

    assert client.get("/v1/products/site-graph", headers=acme["headers"]).json()["site"] == "acme-inbox"
    assert client.get("/v1/products/site-graph", headers=globex["headers"]).json()["site"] == "globex-desk"


def test_failures_are_scoped_per_product(client):
    acme = register(client, "Acme Inbox", ACME)
    globex = register(client, "Globex Desk", GLOBEX)

    run_demo(client, acme, flow="broken_path")
    run_demo(client, globex)  # clean

    assert len(client.get("/v1/products/failures", headers=acme["headers"]).json()) == 1
    assert client.get("/v1/products/failures", headers=globex["headers"]).json() == []


def test_demo_list_is_scoped_per_product(client):
    acme = register(client, "Acme Inbox", ACME)
    globex = register(client, "Globex Desk", GLOBEX)
    run_demo(client, acme)

    assert len(client.get("/v1/demos", headers=acme["headers"]).json()) == 1
    assert client.get("/v1/demos", headers=globex["headers"]).json() == []


def test_pending_corrections_empty_by_default(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.settings, "db_path", tmp_path / "nav.db")
    p = register(client, "Acme Inbox", ACME)
    assert client.get("/v1/products/corrections/pending", headers=p["headers"]).json() == []


def test_approve_and_reject_corrections(client, tmp_path, monkeypatch):
    from navigator.knowledge.memory.pending import PendingCorrectionStore
    from navigator.knowledge.memory.retrieval import retrieve_corrections

    db = tmp_path / "nav.db"
    chroma = tmp_path / "chroma"
    monkeypatch.setattr(app_module.settings, "db_path", db)
    monkeypatch.setattr(app_module.settings, "chroma_path", chroma)
    p = register(client, "Acme Inbox", ACME)

    with PendingCorrectionStore(db) as store:
        row = store.add(
            product_id=p["id"],
            session_id="s1",
            page="inbox",
            tool_call_type="click_element",
            rule="Always wait for toast",
            source_call_id="c1",
        )

    listed = client.get("/v1/products/corrections/pending", headers=p["headers"]).json()
    assert len(listed) == 1
    assert listed[0]["id"] == row.id

    ok = client.post(
        f"/v1/products/corrections/{row.id}/approve",
        headers=p["headers"],
        json={},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved"
    assert client.get("/v1/products/corrections/pending", headers=p["headers"]).json() == []
    hits = retrieve_corrections(p["id"], "toast", page="inbox", path=chroma)
    assert any("toast" in h.rule.lower() for h in hits)

    with PendingCorrectionStore(db) as store:
        row2 = store.add(
            product_id=p["id"],
            session_id="s2",
            page="inbox",
            tool_call_type="fill_field",
            rule="bad idea",
            source_call_id="c2",
        )
    rej = client.post(
        f"/v1/products/corrections/{row2.id}/reject",
        headers=p["headers"],
    )
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"


def test_ingest_knowledge(client, tmp_path, monkeypatch):
    from navigator.knowledge.memory.retrieval import retrieve_product_knowledge

    chroma = tmp_path / "chroma"
    monkeypatch.setattr(app_module.settings, "chroma_path", chroma)
    p = register(client, "Acme Inbox", ACME)
    r = client.post(
        "/v1/products/knowledge",
        headers=p["headers"],
        json={"text": "WhatsApp CRM is a shared inbox for sales teams."},
    )
    assert r.status_code == 201, r.text
    hits = retrieve_product_knowledge(p["id"], "shared inbox", path=chroma)
    assert hits


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
