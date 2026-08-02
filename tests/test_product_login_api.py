"""Product Login dashboard API — public shape never includes plaintext password."""

from __future__ import annotations

from cryptography.fernet import Fernet

from navigator.app import main as app_module
from navigator.app.auth_store import AuthStore
from navigator.app.credential_vault import CredentialVault
from navigator.app.registry import Registry
from navigator.core.settings import settings
from navigator.logs.store import ActionLog
from tests.test_client_dashboard import _cleanup, _client
from test_api import ACME, register


def test_product_login_roundtrip_masks_password(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "credential_key", key)
    vault = CredentialVault(tmp_path / "creds.db")
    app_module.app.dependency_overrides[app_module.get_vault] = lambda: vault

    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    try:
        p = register(client, "Acme Inbox", ACME)
        auth_store.create_user(
            product_id=p["id"], email="test@acme.com", password="password"
        )
        login_resp = client.post(
            "/v1/auth/login",
            json={"email": "test@acme.com", "password": "password"},
            headers={"Host": "localhost"},
        )
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}

        empty = client.get("/client/api/product-login", headers=headers)
        assert empty.status_code == 200
        assert empty.json()["has_password"] is False

        put = client.put(
            "/client/api/product-login",
            json={
                "login_url": "https://acme.example/login",
                "username": "demo@acme.test",
                "password": "super-secret",
                "include_login_in_default_flow": False,
            },
            headers=headers,
        )
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["has_password"] is True
        assert body["username"] == "demo@acme.test"
        assert "super-secret" not in put.text
        assert body.get("password") is None

        got = client.get("/client/api/product-login", headers=headers)
        assert got.json()["has_password"] is True
        assert "super-secret" not in got.text

        # Keep password with null
        keep = client.put(
            "/client/api/product-login",
            json={
                "login_url": "https://acme.example/signin",
                "username": "demo@acme.test",
                "password": None,
                "include_login_in_default_flow": True,
            },
            headers=headers,
        )
        assert keep.status_code == 200
        assert vault.password_for(p["id"]) == "super-secret"
        assert keep.json()["include_login_in_default_flow"] is True
    finally:
        vault.close()
        _cleanup(bundle, prev)
