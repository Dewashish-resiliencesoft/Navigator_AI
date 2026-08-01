from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import bcrypt

class AuthError(Exception):
    pass

class InvalidCredentials(AuthError):
    pass

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_product ON users(product_id);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

class AuthStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if self.db_path.parent != Path():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(_SCHEMA)
            self._local.conn = conn
        return conn

    def create_user(self, product_id: str, email: str, password: str) -> str:
        user_id = f"usr_{secrets.token_urlsafe(16)}"
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode('utf-8')
        now = datetime.now(timezone.utc).isoformat()
        
        try:
            self._conn.execute(
                "INSERT INTO users (user_id, product_id, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, product_id, email, password_hash, now)
            )
        except sqlite3.IntegrityError:
            raise AuthError(f"Email {email} already exists")
            
        return user_id

    def get_user_by_email(self, email: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        
    def get_user(self, user_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

    def create_refresh_token(self, user_id: str, expires_in_seconds: int = 7 * 24 * 3600) -> str:
        token = secrets.token_urlsafe(64)
        token_hash = hash_refresh_token(token)
        
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(now.timestamp() + expires_in_seconds, tz=timezone.utc)
        
        self._conn.execute(
            "INSERT INTO refresh_tokens (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, expires_at.isoformat(), now.isoformat())
        )
        return token

    def consume_refresh_token(self, token: str) -> str:
        """Validates and consumes a refresh token, returning the associated user_id."""
        token_hash = hash_refresh_token(token)
        
        row = self._conn.execute(
            "SELECT user_id, expires_at FROM refresh_tokens WHERE token_hash = ?",
            (token_hash,)
        ).fetchone()
        
        if not row:
            raise AuthError("Invalid refresh token")
            
        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            self.revoke_refresh_token(token)
            raise AuthError("Refresh token expired")
            
        # Revoke the old token (Refresh Token Rotation)
        self.revoke_refresh_token(token)
        
        return row["user_id"]

    def revoke_refresh_token(self, token: str) -> None:
        token_hash = hash_refresh_token(token)
        self._conn.execute("DELETE FROM refresh_tokens WHERE token_hash = ?", (token_hash,))
