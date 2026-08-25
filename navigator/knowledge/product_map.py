"""Product map: bridge between site graph flows and knowledge areas."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from navigator.knowledge.context import ProductMapArea


@dataclass(frozen=True)
class ProductMapRow:
    """One area in the product map, as stored."""

    product_id: str
    area_id: str
    name: str
    purpose: str
    flow_ids: list[str]
    chunk_ids: list[str]
    categories: set[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_area(cls, area: ProductMapArea) -> ProductMapRow:
        return cls(
            product_id=area.product_id,
            area_id=area.area_id,
            name=area.name,
            purpose=area.purpose,
            flow_ids=area.related_flow_ids,
            chunk_ids=area.related_chunk_ids,
            categories=area.categories,
            created_at="",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_area(self) -> ProductMapArea:
        return ProductMapArea(
            product_id=self.product_id,
            area_id=self.area_id,
            name=self.name,
            purpose=self.purpose,
            related_flow_ids=self.flow_ids,
            related_chunk_ids=self.chunk_ids,
            categories=self.categories,
        )


class ProductMapStore:
    """Manage product map areas in the registry database."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(self, area: ProductMapArea) -> None:
        """Insert or update an area."""
        now = datetime.now(timezone.utc).isoformat()
        existing = self._conn.execute(
            "SELECT created_at FROM product_map WHERE product_id = ? AND area_id = ?",
            (area.product_id, area.area_id),
        ).fetchone()
        created_at = existing["created_at"] if existing else now

        self._conn.execute(
            """
            INSERT INTO product_map
            (product_id, area_id, name, purpose, flow_ids, chunk_ids, categories,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (product_id, area_id) DO UPDATE SET
                name = excluded.name,
                purpose = excluded.purpose,
                flow_ids = excluded.flow_ids,
                chunk_ids = excluded.chunk_ids,
                categories = excluded.categories,
                updated_at = excluded.updated_at
            """,
            (
                area.product_id,
                area.area_id,
                area.name,
                area.purpose,
                json.dumps(area.related_flow_ids),
                json.dumps(area.related_chunk_ids),
                json.dumps(sorted(area.categories)),
                created_at,
                now,
            ),
        )

    def get(self, product_id: str, area_id: str) -> ProductMapArea | None:
        row = self._conn.execute(
            "SELECT * FROM product_map WHERE product_id = ? AND area_id = ?",
            (product_id, area_id),
        ).fetchone()
        return None if row is None else self._row_to_area(row)

    def list_product(self, product_id: str) -> list[ProductMapArea]:
        rows = self._conn.execute(
            "SELECT * FROM product_map WHERE product_id = ? ORDER BY updated_at DESC",
            (product_id,),
        ).fetchall()
        return [self._row_to_area(r) for r in rows]

    def delete(self, product_id: str, area_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM product_map WHERE product_id = ? AND area_id = ?",
            (product_id, area_id),
        )
        return cur.rowcount > 0

    @staticmethod
    def _row_to_area(row: sqlite3.Row) -> ProductMapArea:
        return ProductMapArea(
            product_id=row["product_id"],
            area_id=row["area_id"],
            name=row["name"],
            purpose=row["purpose"],
            related_flow_ids=json.loads(row["flow_ids"]),
            related_chunk_ids=json.loads(row["chunk_ids"]),
            categories=set(json.loads(row["categories"])),
        )
