"""JWT signup / login for client dashboard."""

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


def test_signup_login_and_bearer_dashboard(tmp_path):
    client, registry, auth = _app(tmp_path)
    try:
        r = client.post(
            "/v1/auth/signup",
            json={
                "company_name": "Acme Labs",
                "email": "Admin@Acme.com",
                "password": "secretpass",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["access_token"]
        assert body["product_id"]
        assert "refresh_token" in r.cookies

        # email normalized
        assert auth.get_user_by_email("admin@acme.com")

        bio = client.get(
            "/client/api/bio",
            headers={
                "Host": "localhost",
                "Authorization": f"Bearer {body['access_token']}",
            },
        )
        assert bio.status_code == 200, bio.text

        login = client.post(
            "/v1/auth/login",
            json={"email": "admin@acme.com", "password": "secretpass"},
        )
        assert login.status_code == 200
        assert login.json()["product_id"] == body["product_id"]

        dup = client.post(
            "/v1/auth/signup",
            json={
                "company_name": "Other",
                "email": "admin@acme.com",
                "password": "secretpass",
            },
        )
        assert dup.status_code == 409
    finally:
        _cleanup()
        registry.close()
        log_close = getattr(auth, "close", None)
        if callable(log_close):
            log_close()


def test_login_rejects_bad_password(tmp_path):
    client, registry, auth = _app(tmp_path)
    try:
        client.post(
            "/v1/auth/signup",
            json={
                "company_name": "Acme",
                "email": "a@b.com",
                "password": "secretpass",
            },
        )
        bad = client.post(
            "/v1/auth/login",
            json={"email": "a@b.com", "password": "wrongwrong"},
        )
        assert bad.status_code == 401
    finally:
        _cleanup()
        registry.close()
