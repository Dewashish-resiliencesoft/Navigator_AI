"""Demo script dashboard API."""

from __future__ import annotations

import textwrap

from navigator.app import main as app_module
from navigator.app.auth_store import AuthStore
from navigator.app.registry import Registry
from navigator.logs.store import ActionLog
from test_client_dashboard import _cleanup, _client
from test_api import ACME, register


def _graph_yaml() -> str:
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
            flows:
              tour:
                - tool: wait_for
                  selector: body
                  timeout_ms: 5000
                  spoken: Hello
                  expects:
                    check: visible
                    selector: body
        """
    ).strip()


def _auth_headers(client, auth_store, product_id):
    auth_store.create_user(
        product_id=product_id, email="script@acme.com", password="password"
    )
    login = client.post(
        "/v1/auth/login",
        json={"email": "script@acme.com", "password": "password"},
        headers={"Host": "localhost"},
    )
    token = login.json()["access_token"]
    return {"Host": "localhost", "Authorization": f"Bearer {token}"}


def test_demo_script_get_and_patch(tmp_path):
    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    try:
        p = register(client, "Acme Script", ACME)
        headers = _auth_headers(client, auth_store, p["id"])
        registry.put_site_graph(p["id"], _graph_yaml(), "yaml", publish=False)

        got = client.get("/client/api/site-graph/demo-script", headers=headers)
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["beats"]
        assert body["revision"] >= 1
        assert "intake" in [b["kind"] for b in body["beats"]]

        beats = body["beats"]
        for b in beats:
            if b.get("id") == "flow_tour_0":
                b["spoken"] = "Patched line."
                b["spoken_source"] = "manual"
                break

        patched = client.patch(
            "/client/api/site-graph/demo-script",
            json={"beats": beats},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["ok"] is True

        rev = registry.latest_revision(p["id"])
        assert "Patched line." in rev.yaml
        assert "_meta" in rev.yaml and "demo_script" in rev.yaml
    finally:
        _cleanup(bundle, prev)


def test_demo_script_regenerate(tmp_path):
    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    try:
        p = register(client, "Acme Regen", ACME)
        headers = _auth_headers(client, auth_store, p["id"])
        registry.put_site_graph(p["id"], _graph_yaml(), "yaml", publish=False)

        regen = client.post(
            "/client/api/site-graph/demo-script/regenerate", headers=headers
        )
        assert regen.status_code == 200, regen.text
        stats = regen.json().get("stats") or {}
        assert stats.get("beat_count", 0) > 0
    finally:
        _cleanup(bundle, prev)


def test_demo_script_requires_auth(tmp_path):
    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    try:
        p = register(client, "Acme NoAuth", ACME)
        registry.put_site_graph(p["id"], _graph_yaml(), "yaml", publish=False)
        r = client.get(
            "/client/api/site-graph/demo-script",
            headers={"Host": "localhost"},
        )
        assert r.status_code == 401
    finally:
        _cleanup(bundle, prev)
