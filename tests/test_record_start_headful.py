"""Manual record must open a visible Chrome — never follow NAVIGATOR_HEADFUL=0."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from navigator.app import main as app_module
from navigator.app.auth_store import AuthStore
from navigator.app.registry import Registry
from navigator.core.settings import settings
from navigator.logs.store import ActionLog
from test_api import ACME_LIVE, register
from test_demos_start import FakeProvider, SpyRunner


def test_record_start_forces_headful_when_global_headless(tmp_path, monkeypatch):
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

    prev_key = settings.client_api_key
    prev_headful = settings.headful
    settings.client_api_key = "nav_test"
    settings.headful = False  # live demos headless — record must still be headed

    captured: dict = {}

    def fake_start_recorder(**kwargs):
        captured.update(kwargs)
        job = MagicMock()
        job.job_id = "job_1"
        job.flow_id = "demo_flow"
        job.flow_name = kwargs["flow_name"]
        job.phase = "setup"
        job.narration = None
        job.save_mode = kwargs.get("save_mode", "new")
        return job

    monkeypatch.setattr(
        "navigator.app.routers.client_api.start_recorder", fake_start_recorder
    )

    client = TestClient(app_module.app)
    try:
        p = register(client, "Acme Inbox", ACME_LIVE)
        auth_store.create_user(
            product_id=p["id"], email="test@acme.com", password="password"
        )
        login = client.post(
            "/v1/auth/login",
            json={"email": "test@acme.com", "password": "password"},
            headers={"Host": "localhost"},
        )
        assert login.status_code == 200, login.text
        jwt = login.json()["access_token"]
        r = client.post(
            "/client/api/record/start",
            headers={"Host": "localhost", "Authorization": f"Bearer {jwt}"},
            json={
                "start_url": "https://example.com/",
                "flow_name": "onboarding",
                "narrate": False,
                "save_mode": "new",
            },
        )
        assert r.status_code == 200, r.text
        assert captured.get("headful") is True
    finally:
        settings.client_api_key = prev_key
        settings.headful = prev_headful
        app_module.app.dependency_overrides.clear()
        registry.close()
        log.close()
