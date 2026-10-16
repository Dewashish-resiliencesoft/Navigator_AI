"""Mongo-backed dashboard auth (users + refresh tokens)."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

import bcrypt

from navigator.auth.store import (
    DEFAULT_USER_PREFERENCES,
    AuthError,
    hash_refresh_token,
    _merge_preferences,
)
from navigator.storage.mongo import get_db


class _Doc(dict):
    """dict that also supports sqlite3.Row-style ``row['key']`` / ``.keys()``."""

    def keys(self):  # noqa: D401
        return super().keys()


class MongoAuthStore:
    """Same surface as ``AuthStore``, backed by Atlas ``users`` / ``refresh_tokens``."""

    def create_user(self, product_id: str, email: str, password: str) -> str:
        email = email.strip().lower()
        user_id = f"usr_{secrets.token_urlsafe(16)}"
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode("utf-8")
        now = datetime.now(timezone.utc).isoformat()
        db = get_db()
        if db.users.find_one({"email": email}):
            raise AuthError(f"Email {email} already exists")
        db.users.insert_one(
            {
                "user_id": user_id,
                "product_id": product_id,
                "email": email,
                "password_hash": password_hash,
                "created_at": now,
                "preferences_json": "{}",
            }
        )
        return user_id

    def get_user_by_email(self, email: str) -> _Doc | None:
        email = email.strip().lower()
        doc = get_db().users.find_one({"email": email}, {"_id": 0})
        return _Doc(doc) if doc else None

    def get_user(self, user_id: str) -> _Doc | None:
        doc = get_db().users.find_one({"user_id": user_id}, {"_id": 0})
        return _Doc(doc) if doc else None

    def get_preferences(self, user_id: str) -> dict[str, bool]:
        row = self.get_user(user_id)
        if row is None:
            return dict(DEFAULT_USER_PREFERENCES)
        return _merge_preferences(str(row.get("preferences_json") or "{}"))

    def set_preferences(self, user_id: str, patch: dict[str, bool]) -> dict[str, bool]:
        current = self.get_preferences(user_id)
        for key in DEFAULT_USER_PREFERENCES:
            if key in patch:
                current[key] = bool(patch[key])
        get_db().users.update_one(
            {"user_id": user_id},
            {"$set": {"preferences_json": json.dumps(current)}},
        )
        return current

    def create_refresh_token(self, user_id: str, expires_in_seconds: int = 7 * 24 * 3600) -> str:
        token = secrets.token_urlsafe(64)
        token_hash = hash_refresh_token(token)
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(
            now.timestamp() + expires_in_seconds, tz=timezone.utc
        )
        get_db().refresh_tokens.insert_one(
            {
                "token_hash": token_hash,
                "user_id": user_id,
                "expires_at": expires_at.isoformat(),
                "created_at": now.isoformat(),
            }
        )
        return token

    def consume_refresh_token(self, token: str) -> str:
        token_hash = hash_refresh_token(token)
        row = get_db().refresh_tokens.find_one({"token_hash": token_hash})
        if not row:
            raise AuthError("Invalid refresh token")
        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            self.revoke_refresh_token(token)
            raise AuthError("Refresh token expired")
        self.revoke_refresh_token(token)
        return row["user_id"]

    def revoke_refresh_token(self, token: str) -> None:
        get_db().refresh_tokens.delete_one({"token_hash": hash_refresh_token(token)})


def make_auth_store(db_path: Any = None):
    """Auth backend: ``NAVIGATOR_AUTH_BACKEND=sqlite|mongo|auto`` (default auto)."""
    import os

    from navigator.auth.store import AuthStore

    backend = (os.environ.get("NAVIGATOR_AUTH_BACKEND") or "auto").strip().lower()
    if backend == "sqlite":
        print("[storage] AuthStore → SQLite (NAVIGATOR_AUTH_BACKEND=sqlite)", flush=True)
        return AuthStore(db_path)

    if backend in ("mongo", "auto"):
        from navigator.storage.mongo import mongo_enabled, ping

        if mongo_enabled():
            try:
                ping()
                print("[storage] AuthStore → MongoDB Atlas", flush=True)
                return MongoAuthStore()
            except Exception as exc:  # noqa: BLE001
                if backend == "mongo":
                    raise
                print(
                    f"[storage] Atlas unreachable ({exc}); AuthStore → SQLite fallback",
                    flush=True,
                )
    return AuthStore(db_path)
