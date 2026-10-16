"""SQLite → Mongo Atlas bulk copy (tables → collections)."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

from navigator.storage.env_load import load_cloud_env
from navigator.storage.mongo import get_db, ping


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in cur.fetchall()]


def migrate(
    *,
    navigator_db: Path,
    registry_db: Path,
    replace: bool = False,
) -> dict[str, int]:
    load_cloud_env()
    ping()
    db = get_db()
    counts: dict[str, int] = {}

    mapping: list[tuple[Path, str]] = [
        (navigator_db, "users"),
        (navigator_db, "refresh_tokens"),
        (navigator_db, "action_log"),
        (navigator_db, "demo_runs"),
        (navigator_db, "session_tokens"),
        (navigator_db, "pending_corrections"),
        (navigator_db, "decision_trace"),
        (navigator_db, "llm_token_usage"),
        (registry_db, "products"),
        (registry_db, "site_graph_revisions"),
        (registry_db, "product_map"),
    ]

    for path, table in mapping:
        if not path.is_file():
            counts[table] = 0
            continue
        with sqlite3.connect(path) as conn:
            docs = _rows(conn, table)
        if replace and docs:
            db[table].delete_many({})
        if not docs:
            counts[table] = 0
            continue
        # upsert-ish: insert many; skip dupes on unique indexes
        inserted = 0
        for doc in docs:
            try:
                db[table].insert_one(doc)
                inserted += 1
            except Exception:  # noqa: BLE001
                # duplicate key — update if users by email
                if table == "users" and "email" in doc:
                    db.users.update_one(
                        {"email": doc["email"]}, {"$set": doc}, upsert=True
                    )
                    inserted += 1
                elif table == "products" and "product_id" in doc:
                    db.products.update_one(
                        {"product_id": doc["product_id"]}, {"$set": doc}, upsert=True
                    )
                    inserted += 1
        counts[table] = inserted
    return counts


def main() -> None:
    p = argparse.ArgumentParser(description="Copy SQLite tables into Mongo Atlas")
    p.add_argument("--navigator-db", default="navigator.db")
    p.add_argument("--registry-db", default="registry.db")
    p.add_argument("--replace", action="store_true")
    args = p.parse_args()
    counts = migrate(
        navigator_db=Path(args.navigator_db),
        registry_db=Path(args.registry_db),
        replace=args.replace,
    )
    for k, v in counts.items():
        print(f"{k}: {v}")
    print("done")


if __name__ == "__main__":
    main()
