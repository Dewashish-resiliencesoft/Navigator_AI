"""Local operator console: loopback-only control UI for live demos."""

from __future__ import annotations

from fastapi.testclient import TestClient

from navigator.app import main as app_module
from navigator.app.registry import Registry
from navigator.app.runner import DemoRunner
from navigator.logs.store import ActionLog
from navigator.app.auth_store import AuthStore
from navigator.core.settings import settings
from test_api import ACME, register
from test_demos_start import FakeProvider, SpyRunner


def _client(tmp_path, *, client_api_key: str | None = None):
    registry = Registry(tmp_path / "registry.db")
    log = ActionLog(tmp_path / "actions.db")
    auth_store = AuthStore(tmp_path / "auth.db")
    runner = SpyRunner(
        str(tmp_path / "actions.db"), headful=False, archive_dir=tmp_path / "archives"
    )
    provider = FakeProvider(platform="zoom")

    app_module.app.dependency_overrides[app_module.get_registry] = lambda: registry
    app_module.app.dependency_overrides[app_module.get_log] = lambda: log
    app_module.app.dependency_overrides[app_module.get_auth_store] = lambda: auth_store
    app_module.app.dependency_overrides[app_module.get_runner] = lambda: runner
    app_module.app.dependency_overrides[app_module.get_provider_factory] = (
        lambda: (lambda platform=None: provider)
    )

    prev = settings.client_api_key
    if client_api_key is not None:
        settings.client_api_key = client_api_key

    client = TestClient(app_module.app)
    client.runner = runner
    client.provider = provider
    client.registry = registry
    return client, prev, registry, log, auth_store


def _cleanup(client_bundle, prev_key):
    _, _, registry, log, auth_store = client_bundle
    settings.client_api_key = prev_key
    app_module.app.dependency_overrides.clear()
    registry.close()
    log.close()


def test_client_page_ok_on_localhost(tmp_path):
    bundle = _client(tmp_path, client_api_key="nav_test")
    client, prev, registry, log, auth_store = bundle
    try:
        r = client.get("/client", headers={"Host": "localhost"})
        assert r.status_code == 200
        assert "Navigator AI" in r.text
        # SPA shell: the React app mounts here. Falls back to a build-me page
        # when web/dist is absent, which is still a valid 200.
        assert '<div id="root">' in r.text or "Console not built" in r.text
    finally:
        _cleanup(bundle, prev)


def test_client_page_forbidden_on_public_host(tmp_path):
    bundle = _client(tmp_path, client_api_key="nav_test")
    client, prev, registry, log, auth_store = bundle
    try:
        r = client.get("/client", headers={"Host": "evil.trycloudflare.com"})
        assert r.status_code == 403
    finally:
        _cleanup(bundle, prev)


def test_client_api_start_uses_server_key(tmp_path):
    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    try:
        p = register(client, "Acme Inbox", ACME)
        # register() returns headers with Token; extract key
        key = p["headers"]["Authorization"].split(None, 1)[1]
        
        auth_store.create_user(product_id=p["id"], email="test@acme.com", password="password")
        login_resp = client.post("/v1/auth/login", json={"email": "test@acme.com", "password": "password"}, headers={"Host": "localhost"})
        assert login_resp.status_code == 200, login_resp.text
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}
        r = client.post(
            "/client/api/demos/start",
            json={
                "platform": "zoom",
                "page_id": "main",
                "flow_id": "happy_path",
                "intake": {"name": "Dewa", "company": "Acme"},
            },
            headers=headers,
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["meeting"]["url"].startswith("https://meet.example/")
        assert client.runner.live_calls[0]["intake_prefill"]["name"] == "Dewa"
    finally:
        settings.client_api_key = prev
        app_module.app.dependency_overrides.clear()
        registry.close()
        log.close()


def test_client_api_forbidden_without_loopback(tmp_path):
    bundle = _client(tmp_path, client_api_key="nav_x")
    client, prev, registry, log, auth_store = bundle
    try:
        r = client.get("/client/api/demos", headers={"Host": "public.example"})
        assert r.status_code == 401
    finally:
        _cleanup(bundle, prev)


def test_client_api_requires_ops_key(tmp_path):
    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    try:
        r = client.get("/client/api/demos", headers={"Host": "127.0.0.1"})
        assert r.status_code == 401
    finally:
        _cleanup(bundle, prev)


def test_client_bio_and_knowledge_roundtrip(tmp_path, monkeypatch):
    import navigator.knowledge.company_bio as cb
    import navigator.knowledge.product_brief as pb

    monkeypatch.setattr(cb, "_ROOT", tmp_path)
    monkeypatch.setattr(pb, "_ROOT", tmp_path)
    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    try:
        p = register(client, "Acme Inbox", ACME)
        key = p["headers"]["Authorization"].split(None, 1)[1]
        auth_store.create_user(product_id=p["id"], email="test@acme.com", password="password")
        login_resp = client.post("/v1/auth/login", json={"email": "test@acme.com", "password": "password"}, headers={"Host": "localhost"})
        assert login_resp.status_code == 200, login_resp.text
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}
        r = client.put(
            "/client/api/bio",
            json={
                "fields": [
                    {"key": "company_name", "label": "Company name", "value": "Acme Co"},
                    {"key": "about", "label": "About", "value": "We sell widgets"},
                ]
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["fields"][0]["value"] == "Acme Co"
        got = client.get("/client/api/bio", headers=headers)
        assert got.json()["fields"][1]["value"] == "We sell widgets"

        k = client.put(
            "/client/api/knowledge",
            json={"markdown": "# Tone\nBe warm.\n"},
            headers=headers,
        )
        assert k.status_code == 200, k.text
        assert "warm" in k.json()["markdown"]
    finally:
        settings.client_api_key = prev
        app_module.app.dependency_overrides.clear()
        registry.close()
        log.close()


def test_client_flows_playlist_save(tmp_path):
    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    try:
        p = register(client, "Acme Inbox", ACME)
        key = p["headers"]["Authorization"].split(None, 1)[1]
        auth_store.create_user(product_id=p["id"], email="test@acme.com", password="password")
        login_resp = client.post("/v1/auth/login", json={"email": "test@acme.com", "password": "password"}, headers={"Host": "localhost"})
        assert login_resp.status_code == 200, login_resp.text
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}
        r = client.put(
            "/client/api/flows",
            json={
                "playlist": [
                    {
                        "order": 1,
                        "name": "Happy path",
                        "page_id": "main",
                        "flow_id": "happy_path",
                    }
                ]
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["playlist"][0]["name"] == "Happy path"
        listed = client.get("/client/api/flows", headers=headers)
        assert listed.json()["playlist"][0]["flow_id"] == "happy_path"
    finally:
        settings.client_api_key = prev
        app_module.app.dependency_overrides.clear()
        registry.close()
        log.close()


def test_client_bootstrap_sets_key(tmp_path):
    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    try:
        r = client.post("/client/api/bootstrap", headers={"Host": "localhost"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["api_key"].startswith("nav_")
        assert settings.client_api_key == body["api_key"]
        
        auth_store.create_user(product_id=body["product_id"], email="test@acme.com", password="password")
        login_resp = client.post("/v1/auth/login", json={"email": "test@acme.com", "password": "password"}, headers={"Host": "localhost"})
        assert login_resp.status_code == 200, login_resp.text
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}
        
        listed = client.get("/client/api/demos", headers=headers)
        assert listed.status_code == 200
    finally:
        _cleanup(bundle, prev)


def test_client_product_domain_updates_site_graph_base_url(tmp_path):
    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    prev_product = settings.product_url
    try:
        boot = client.post("/client/api/bootstrap", headers={"Host": "localhost"})
        assert boot.status_code == 200
        boot_body = boot.json()

        auth_store.create_user(product_id=boot_body["product_id"], email="test@acme.com", password="password")
        login_resp = client.post("/v1/auth/login", json={"email": "test@acme.com", "password": "password"}, headers={"Host": "localhost"})
        assert login_resp.status_code == 200, login_resp.text
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}

        before = client.get("/client/api/product-domain", headers=headers)
        assert before.status_code == 200, before.text
        assert before.json()["placeholder"] is True

        bad = client.put(
            "/client/api/product-domain",
            json={"base_url": "https://example.com/"},
            headers=headers,
        )
        assert bad.status_code == 422

        ok = client.put(
            "/client/api/product-domain",
            json={"base_url": "https://app.acme.test/login"},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["base_url"] == "https://app.acme.test/"
        assert body["placeholder"] is False
        assert settings.product_url.startswith("https://app.acme.test")

        again = client.get("/client/api/product-domain", headers=headers)
        assert again.json()["base_url"] == "https://app.acme.test/"
    finally:
        settings.product_url = prev_product
        _cleanup(bundle, prev)


def test_client_runs_list_empty_and_scoped(tmp_path):
    from datetime import datetime, timezone
    from uuid import uuid4

    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    try:
        p = register(client, "Acme Inbox", ACME)
        auth_store.create_user(
            product_id=p["id"], email="runs@acme.com", password="password"
        )
        login_resp = client.post(
            "/v1/auth/login",
            json={"email": "runs@acme.com", "password": "password"},
            headers={"Host": "localhost"},
        )
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}

        empty = client.get("/client/api/runs", headers=headers)
        assert empty.status_code == 200
        assert empty.json() == []

        sid = uuid4()
        log.upsert_run(
            session_id=sid,
            demo_id=uuid4(),
            product_id=p["id"],
            platform="static",
            status="finished",
            origin="public_embed",
            host_os="Linux",
            host_release="7",
            host_machine="x86_64",
            host_name="box",
            browser="",
            meeting_label="meet:abc",
            started_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        )
        other = uuid4()
        log.upsert_run(
            session_id=other,
            demo_id=uuid4(),
            product_id="other-tenant",
            platform="zoom",
            status="finished",
            origin="public_embed",
            host_os="Linux",
            host_release="7",
            host_machine="x86_64",
            host_name="box",
            browser="",
            meeting_label="zoom",
            started_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        )

        listed = client.get("/client/api/runs?days=7", headers=headers)
        assert listed.status_code == 200, listed.text
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["session_id"] == str(sid)

        one = client.get(f"/client/api/runs/{sid}", headers=headers)
        assert one.status_code == 200
        assert one.json()["platform"] == "static"

        denied = client.get(f"/client/api/runs/{other}", headers=headers)
        assert denied.status_code == 404

        events = client.get(f"/client/api/runs/{sid}/events", headers=headers)
        assert events.status_code == 200
        assert events.json() == []
    finally:
        _cleanup(bundle, prev)
