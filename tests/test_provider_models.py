"""Provider model listing helpers and dashboard API."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from navigator.app import main as app_module
from navigator.app.auth_store import AuthStore
from navigator.app.credential_vault import CredentialVault
from navigator.app.registry import Registry
from navigator.app.runner import DemoRunner
from navigator.client.provider_models import list_groq_models, list_provider_models
from navigator.logs.store import ActionLog


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIGATOR_CREDENTIAL_KEY", "test-credential-key-32bytes-long!!")
    registry = Registry(tmp_path / "registry.db")
    log = ActionLog(tmp_path / "actions.db")
    auth = AuthStore(tmp_path / "auth.db")
    runner = DemoRunner(str(tmp_path / "actions.db"), redis_url=None)
    vault = CredentialVault(tmp_path / "vault.db")

    app_module.app.dependency_overrides[app_module.get_registry] = lambda: registry
    app_module.app.dependency_overrides[app_module.get_log] = lambda: log
    app_module.app.dependency_overrides[app_module.get_auth_store] = lambda: auth
    app_module.app.dependency_overrides[app_module.get_runner] = lambda: runner
    app_module.app.dependency_overrides[app_module.get_vault] = lambda: vault

    client = TestClient(app_module.app)
    return client, registry, vault


def _cleanup():
    app_module.app.dependency_overrides.clear()


def _signup(client: TestClient) -> tuple[str, str]:
    r = client.post(
        "/v1/auth/signup",
        json={
            "company_name": "Models Co",
            "email": "models@example.com",
            "password": "secretpass",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["access_token"], body["product_id"]


def test_list_groq_models_tags():
    whisper = MagicMock(id="whisper-large-v3-turbo")
    chat = MagicMock(id="llama-3.3-70b-versatile")
    resp = MagicMock(data=[whisper, chat])

    mock_client = MagicMock()
    mock_client.models.list.return_value = resp

    with patch("navigator.core.groq_client.groq_client", return_value=mock_client):
        models = list_groq_models("gsk_test")

    assert models[0]["id"] == "llama-3.3-70b-versatile"
    assert models[1]["id"] == "whisper-large-v3-turbo"
    assert models[1]["tags"] == ["stt"]


def test_list_provider_models_requires_key():
    with pytest.raises(ValueError, match="API key required"):
        list_provider_models("gemini", "")


def test_agent_provider_models_api(tmp_path, monkeypatch):
    client, _registry, vault = _app(tmp_path, monkeypatch)
    try:
        token, product_id = _signup(client)
        headers = {"Host": "localhost", "Authorization": f"Bearer {token}"}
        vault.put_provider_keys(product_id, gemini_api_key="gem-test")

        fake = [
            {"id": "gemini-2.0-flash", "label": "Flash", "tags": ["chat"]},
            {"id": "gemini-live", "label": "Live", "tags": ["live"]},
        ]
        with patch(
            "navigator.client.provider_models.list_provider_models",
            return_value=fake,
        ):
            got = client.get(
                "/client/api/agent-provider-models?provider=gemini",
                headers=headers,
            )
            assert got.status_code == 200, got.text
            body = got.json()
            assert body["ok"] is True
            assert body["provider"] == "gemini"
            assert len(body["models"]) == 2

            preview = client.post(
                "/client/api/agent-provider-models",
                headers=headers,
                json={"provider": "groq", "api_key": "gsk_preview"},
            )
            assert preview.status_code == 200, preview.text
            assert preview.json()["provider"] == "groq"
    finally:
        _cleanup()
