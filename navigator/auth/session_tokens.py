import hashlib
import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict

class SessionTokenError(Exception):
    pass

class RateLimitExceeded(SessionTokenError):
    pass

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_tokens (
    token_hash TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    intake_json TEXT,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_tokens_product ON session_tokens (product_id);
"""

def hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()

class SessionTokenStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if self.db_path.parent != Path():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # Simple in-memory rate limiter: product_id -> [timestamps]
        self._rate_limits: dict[str, list[float]] = {}
        self._rl_lock = threading.Lock()

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

    def _check_rate_limit(self, product_id: str) -> None:
        now = time.time()
        with self._rl_lock:
            history = self._rate_limits.get(product_id, [])
            # Keep only last 60 seconds
            history = [ts for ts in history if now - ts < 60]
            if len(history) >= 100:  # 100 tokens per minute per product
                self._rate_limits[product_id] = history
                raise RateLimitExceeded("Too many session tokens requested.")
            history.append(now)
            self._rate_limits[product_id] = history

    def create_token(
        self, product_id: str, intake: Optional[Dict] = None, expires_in_seconds: int = 3600
    ) -> tuple[str, datetime]:
        self._check_rate_limit(product_id)
        
        raw_token = f"sess_{secrets.token_urlsafe(32)}"
        token_hash = hash_key(raw_token)
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(now.timestamp() + expires_in_seconds, tz=timezone.utc)
        
        intake_json = json.dumps(intake) if intake else None

        self._conn.execute(
            "INSERT INTO session_tokens (token_hash, product_id, intake_json, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token_hash, product_id, intake_json, expires_at.isoformat(), now.isoformat())
        )
        return raw_token, expires_at

    def consume_token(self, raw_token: str) -> dict:
        """Validates and consumes a session token. Returns a dict with product_id and intake."""
        token_hash = hash_key(raw_token)
        
        row = self._conn.execute(
            "SELECT product_id, intake_json, expires_at, used_at FROM session_tokens WHERE token_hash = ?",
            (token_hash,)
        ).fetchone()
        
        if not row:
            raise SessionTokenError("Invalid session token.")
            
        if row["used_at"] is not None:
            raise SessionTokenError("Session token already used.")
            
        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            raise SessionTokenError("Session token expired.")
            
        # Mark as used (atomically)
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "UPDATE session_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
            (now, token_hash)
        )
        if cursor.rowcount == 0:
            # Another request raced us and won
            raise SessionTokenError("Session token already used.")
            
        intake = json.loads(row["intake_json"]) if row["intake_json"] else None
        
        return {
            "product_id": row["product_id"],
            "intake": intake
        }
