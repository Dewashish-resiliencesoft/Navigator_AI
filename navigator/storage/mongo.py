"""MongoDB Atlas client for Navigator.

Auth + image metadata live here when ``MONGODB_URI`` / ``NAVIGATOR_MONGODB_URI``
is set. Call ``load_cloud_env()`` before first use (done in ``get_db``).
"""

from __future__ import annotations

import os
import threading
from typing import Any

from navigator.storage.env_load import load_cloud_env

_lock = threading.Lock()
_client: Any = None
_db: Any = None


def mongodb_uri() -> str:
    load_cloud_env()
    return (
        os.environ.get("NAVIGATOR_MONGODB_URI")
        or os.environ.get("MONGODB_URI")
        or ""
    ).strip()


def mongo_enabled() -> bool:
    return bool(mongodb_uri())


def get_client():
    global _client
    uri = mongodb_uri()
    if not uri:
        raise RuntimeError("MONGODB_URI not set (atlas-credentials.env)")
    with _lock:
        if _client is None:
            from pymongo import MongoClient

            _client = MongoClient(
                uri,
                serverSelectionTimeoutMS=8_000,
                connectTimeoutMS=8_000,
                socketTimeoutMS=20_000,
            )
        return _client


def get_db(name: str = "navigator"):
    """Return the Navigator database (default ``navigator``)."""
    global _db
    with _lock:
        if _db is None:
            client = get_client()
            try:
                default = client.get_default_database()
            except Exception:  # noqa: BLE001
                default = None
            _db = default if default is not None else client[name]
        return _db


def ensure_indexes() -> None:
    """Create indexes once at startup (never on the request path)."""
    db = get_db()
    try:
        db.users.create_index("email", unique=True)
        db.users.create_index("user_id", unique=True)
        db.users.create_index("product_id")
        db.refresh_tokens.create_index("token_hash", unique=True)
        db.images.create_index([("product_id", 1), ("created_at", -1)])
        db.images.create_index("public_id", unique=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[storage] index ensure skipped: {exc}", flush=True)


def ping() -> dict:
    return get_client().admin.command("ping")
