"""Per-user dashboard preferences (Get started visibility)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from navigator.app import main as app_module
from navigator.app.auth_store import AuthStore
from navigator.app.registry import Registry
from navigator.app.runner import DemoRunner
from navigator.logs.store import ActionLog


def _app(tmp_path):
    registry = Registry(tmp_path / "registry.db")
    log = ActionLog(tmp_path / "actions.db")
    auth = AuthStore(tmp_path / "auth.db")
    runner = DemoRunner(str(tmp_path / "actions.db"), redis_url=None)

    app_module.app.dependency_overrides[app_module.get_registry] = lambda: registry
    app_module.app.dependency_overrides[app_module.get_log] = lambda: log
    app_module.app.dependency_overrides[app_module.get_auth_store] = lambda: auth
    app_module.app.dependency_overrides[app_module.get_runner] = lambda: runner

    client = TestClient(app_module.app)
    return client, registry, auth


def _cleanup():
    app_module.app.dependency_overrides.clear()


def _signup(client: TestClient) -> str:
    r = client.post(
        "/v1/auth/signup",
        json={
            "company_name": "Prefs Co",
            "email": "prefs@example.com",
            "password": "secretpass",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def test_user_preferences_defaults_and_patch(tmp_path):
    client, registry, auth = _app(tmp_path)
    try:
        token = _signup(client)
        headers = {"Host": "localhost", "Authorization": f"Bearer {token}"}

        got = client.get("/client/api/user/preferences", headers=headers)
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["hide_get_started_card"] is False
        assert body["onboarding_wizard_dismissed"] is False
        assert body["onboarding_wizard_completed"] is False
        assert body["email"] == "prefs@example.com"
        assert body["product_name"] == "Prefs Co"
        assert body["product_id"] == "prefs-co"

        me = client.get("/client/api/account", headers=headers)
        assert me.status_code == 200, me.text
        assert me.json() == {
            "email": "prefs@example.com",
            "product_name": "Prefs Co",
            "product_id": "prefs-co",
        }
        # Regression: signup email + company land on account for Sidebar chip.
        assert me.json()["email"].endswith("@example.com")
        assert len(me.json()["product_name"]) > 0
        assert registry.get("prefs-co").name == "Prefs Co"

        user = auth.get_user_by_email("prefs@example.com")
        assert user is not None
        assert auth.get_preferences(user["user_id"]) == {
            "hide_get_started_card": False,
            "onboarding_wizard_dismissed": False,
            "onboarding_wizard_completed": False,
        }

        put = client.put(
            "/client/api/user/preferences",
            headers=headers,
            json={"hide_get_started_card": True},
        )
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["hide_get_started_card"] is True
        assert body["onboarding_wizard_dismissed"] is False
        assert body["email"] == "prefs@example.com"
        assert body["product_name"] == "Prefs Co"

        again = client.get("/client/api/user/preferences", headers=headers)
        assert again.json()["hide_get_started_card"] is True

        dismiss = client.put(
            "/client/api/user/preferences",
            headers=headers,
            json={
                "onboarding_wizard_dismissed": True,
                "hide_get_started_card": True,
            },
        )
        assert dismiss.status_code == 200
        assert dismiss.json()["onboarding_wizard_dismissed"] is True
    finally:
        _cleanup()
        registry.close()


def test_user_preferences_requires_auth(tmp_path):
    client, registry, _auth = _app(tmp_path)
    try:
        r = client.get(
            "/client/api/user/preferences",
            headers={"Host": "localhost"},
        )
        assert r.status_code == 401
    finally:
        _cleanup()
        registry.close()
