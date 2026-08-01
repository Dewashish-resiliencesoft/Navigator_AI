"""Local operator console: loopback-only control UI for live demos."""

from __future__ import annotations

from fastapi.testclient import TestClient

from navigator.app import main as app_module
from navigator.app.registry import Registry
from navigator.app.runner import DemoRunner
from navigator.logs.store import ActionLog
from navigator.core.settings import settings
from test_api import ACME, register
from test_demos_start import FakeProvider, SpyRunner


def _client(tmp_path, *, client_api_key: str | None = None):
    registry = Registry(tmp_path / "registry.db")
    log = ActionLog(tmp_path / "actions.db")
    runner = SpyRunner(
        str(tmp_path / "actions.db"), headful=False, archive_dir=tmp_path / "archives"
    )
    provider = FakeProvider(platform="zoom")

    app_module.app.dependency_overrides[app_module.get_registry] = lambda: registry
    app_module.app.dependency_overrides[app_module.get_log] = lambda: log
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
    return client, prev, registry, log


def _cleanup(client_bundle, prev_key):
    _, _, registry, log = client_bundle
    settings.client_api_key = prev_key
    app_module.app.dependency_overrides.clear()
    registry.close()
    log.close()


def test_client_page_ok_on_localhost(tmp_path):
    bundle = _client(tmp_path, client_api_key="nav_test")
    client, prev, *_ = bundle
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
    client, prev, *_ = bundle
    try:
        r = client.get("/client", headers={"Host": "evil.trycloudflare.com"})
        assert r.status_code == 403
    finally:
        _cleanup(bundle, prev)


def test_client_api_start_uses_server_key(tmp_path):
    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log = bundle
    try:
        p = register(client, "Acme Inbox", ACME)
        # register() returns headers with Token; extract key
        key = p["headers"]["Authorization"].split(None, 1)[1]
        settings.client_api_key = key
        r = client.post(
            "/client/api/demos/start",
            json={
                "platform": "zoom",
                "page_id": "main",
                "flow_id": "happy_path",
                "intake": {"name": "Dewa", "company": "Acme"},
            },
            headers={"Host": "localhost"},
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
    client, prev, *_ = bundle
    try:
        r = client.get("/client/api/demos", headers={"Host": "public.example"})
        assert r.status_code == 403
    finally:
        _cleanup(bundle, prev)


def test_client_api_requires_ops_key(tmp_path):
    bundle = _client(tmp_path, client_api_key="")
    client, prev, *_ = bundle
    try:
        r = client.get("/client/api/demos", headers={"Host": "127.0.0.1"})
        assert r.status_code == 503
        assert "CLIENT_API_KEY" in r.text or "ops" in r.text.lower()
    finally:
        _cleanup(bundle, prev)


def test_client_bio_and_knowledge_roundtrip(tmp_path, monkeypatch):
    import navigator.knowledge.company_bio as cb
    import navigator.knowledge.product_brief as pb

    monkeypatch.setattr(cb, "_ROOT", tmp_path)
    monkeypatch.setattr(pb, "_ROOT", tmp_path)
    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log = bundle
    try:
        p = register(client, "Acme Inbox", ACME)
        key = p["headers"]["Authorization"].split(None, 1)[1]
        settings.client_api_key = key
        r = client.put(
            "/client/api/bio",
            json={
                "fields": [
                    {"key": "company_name", "label": "Company name", "value": "Acme Co"},
                    {"key": "about", "label": "About", "value": "We sell widgets"},
                ]
            },
            headers={"Host": "localhost"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["fields"][0]["value"] == "Acme Co"
        got = client.get("/client/api/bio", headers={"Host": "localhost"})
        assert got.json()["fields"][1]["value"] == "We sell widgets"

        k = client.put(
            "/client/api/knowledge",
            json={"markdown": "# Tone\nBe warm.\n"},
            headers={"Host": "localhost"},
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
    client, prev, registry, log = bundle
    try:
        p = register(client, "Acme Inbox", ACME)
        key = p["headers"]["Authorization"].split(None, 1)[1]
        settings.client_api_key = key
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
            headers={"Host": "localhost"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["playlist"][0]["name"] == "Happy path"
        listed = client.get("/client/api/flows", headers={"Host": "localhost"})
        assert listed.json()["playlist"][0]["flow_id"] == "happy_path"
    finally:
        settings.client_api_key = prev
        app_module.app.dependency_overrides.clear()
        registry.close()
        log.close()


def test_client_bootstrap_sets_key(tmp_path):
    bundle = _client(tmp_path, client_api_key="")
    client, prev, *_ = bundle
    try:
        r = client.post("/client/api/bootstrap", headers={"Host": "localhost"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["api_key"].startswith("nav_")
        assert settings.client_api_key == body["api_key"]
        listed = client.get("/client/api/demos", headers={"Host": "localhost"})
        assert listed.status_code == 200
    finally:
        _cleanup(bundle, prev)
