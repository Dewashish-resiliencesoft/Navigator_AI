"""Credential vault: encrypt at rest, never leak password via public()."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from navigator.app.credential_vault import (
    CredentialVault,
    CredentialVaultError,
    VaultNotConfigured,
)
from navigator.core.settings import settings


@pytest.fixture()
def fernet_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "credential_key", key)
    return key


def test_put_get_roundtrip(tmp_path, fernet_key):
    vault = CredentialVault(tmp_path / "creds.db")
    vault.put(
        "acme",
        login_url="https://acme.example/login",
        username="demo@acme.test",
        password="s3cret",
        include_login_in_default_flow=True,
    )
    pub = vault.public("acme")
    assert pub["has_password"] is True
    assert pub["username"] == "demo@acme.test"
    assert "password" not in pub
    assert "s3cret" not in str(pub)
    assert pub["include_login_in_default_flow"] is True
    assert vault.password_for("acme") == "s3cret"
    url, user, pwd = vault.credentials_for("acme")
    assert (url, user, pwd) == (
        "https://acme.example/login",
        "demo@acme.test",
        "s3cret",
    )
    vault.close()


def test_keep_existing_password_with_none(tmp_path, fernet_key):
    vault = CredentialVault(tmp_path / "creds.db")
    vault.put("acme", login_url="", username="a", password="one")
    vault.put("acme", login_url="/login", username="b", password=None)
    assert vault.password_for("acme") == "one"
    assert vault.public("acme")["username"] == "b"
    vault.close()


def test_missing_key_fails_loud(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "credential_key", "")
    vault = CredentialVault(tmp_path / "creds.db")
    with pytest.raises(VaultNotConfigured):
        vault.put("acme", login_url="", username="a", password="x")
    vault.close()


def test_first_save_requires_password(tmp_path, fernet_key):
    vault = CredentialVault(tmp_path / "creds.db")
    with pytest.raises(CredentialVaultError):
        vault.put("acme", login_url="", username="a", password=None)
    vault.close()
