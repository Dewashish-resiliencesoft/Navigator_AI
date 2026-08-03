"""Product registry: who we demo for, and which site graph revision to use.

The registry is what turns Navigator from "a demo agent for one CRM" into a
service. A product is registered once, its site graph is uploaded (and validated
before it is ever stored), and each revision is kept -- uploads never overwrite,
so a demo that worked last week is reproducible and a bad upload is one pointer
move away from being undone.

SQLite, same reasoning as the ActionLog: this is a handful of small tables with
one indexed read path. Postgres when the deployment needs concurrent writers.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from navigator.knowledge.site_graph import SiteGraph, SiteGraphError, parse_site_graph

SiteGraphSource = Literal["yaml", "recorded", "sdk"]
"""How a revision was authored. Provenance only -- all three are treated alike,
because a human wrote or approved the postconditions either way."""


class Product(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_id: str
    name: str
    created_at: datetime
    active_revision: int | None = None
    """Which site graph revision demos use. None until the first upload."""


class SiteGraphRevision(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_id: str
    revision: int
    source: SiteGraphSource
    yaml: str
    """The exact text uploaded. Kept verbatim so a revision is auditable."""
    created_at: datetime
    site: str
    graph_version: int
    """The `version` field inside the graph itself, which the customer controls."""
    published: bool = False
    """True once this revision has been the product's active revision.

    A draft is editable and testable but invisible to End Users; only a published
    revision may serve a live demo. See docs/PRODUCT_MODEL.md."""


class NewProduct(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    product_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    """Slug used in Chroma collection names and log rows. Derived from `name`
    when omitted."""


class RegisteredProduct(BaseModel):
    """Registration response. The API key is shown exactly once."""

    product: Product
    api_key: str


class RegistryError(Exception):
    pass


class ProductNotFound(RegistryError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id       TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    api_key_hash     TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    active_revision  INTEGER
);
CREATE TABLE IF NOT EXISTS site_graph_revisions (
    product_id     TEXT NOT NULL,
    revision       INTEGER NOT NULL,
    source         TEXT NOT NULL,
    yaml           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    site           TEXT NOT NULL,
    graph_version  INTEGER NOT NULL,
    published      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (product_id, revision)
);
CREATE INDEX IF NOT EXISTS products_api_key ON products (api_key_hash);
CREATE TABLE IF NOT EXISTS product_map (
    product_id     TEXT NOT NULL,
    area_id        TEXT NOT NULL,
    name           TEXT NOT NULL,
    purpose        TEXT NOT NULL,
    flow_ids       TEXT NOT NULL,
    chunk_ids      TEXT NOT NULL,
    categories     TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (product_id, area_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);
CREATE INDEX IF NOT EXISTS product_map_product ON product_map (product_id);
"""


def hash_key(api_key: str) -> str:
    """Store only a hash. A leaked registry DB must not yield usable keys."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def _slug(name: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:64] or "product"


class Registry:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if self.db_path.parent != Path():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # One connection per thread, same reasoning as ActionLog: API handlers run
        # in a threadpool and sqlite3 forbids sharing a connection across threads.
        self._local = threading.local()
        self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add site_graph_revisions.published to a pre-draft-model DB.

        Every revision used to be activated on upload, so existing revisions were
        all reachable by live demos -- backfilling them to published keeps that
        true instead of silently taking a tenant's live demo away.
        """
        cols = {
            r["name"]
            for r in self._conn.execute(
                "PRAGMA table_info(site_graph_revisions)"
            ).fetchall()
        }
        if "published" not in cols:
            self._conn.execute(
                "ALTER TABLE site_graph_revisions ADD COLUMN published "
                "INTEGER NOT NULL DEFAULT 1"
            )

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    # -- products ------------------------------------------------------------

    def register(self, spec: NewProduct) -> RegisteredProduct:
        product_id = spec.product_id or _slug(spec.name)
        api_key = f"nav_{secrets.token_urlsafe(32)}"
        now = datetime.now(timezone.utc)

        try:
            self._conn.execute(
                "INSERT INTO products (product_id, name, api_key_hash, created_at) "
                "VALUES (?,?,?,?)",
                (product_id, spec.name, hash_key(api_key), now.isoformat()),
            )
        except sqlite3.IntegrityError:
            raise RegistryError(f"product_id {product_id!r} is already registered") from None

        return RegisteredProduct(
            product=Product(product_id=product_id, name=spec.name, created_at=now),
            api_key=api_key,
        )

    def get(self, product_id: str) -> Product:
        row = self._conn.execute(
            "SELECT * FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()
        if row is None:
            raise ProductNotFound(f"no such product: {product_id}")
        return _to_product(row)

    def authenticate(self, api_key: str) -> Product:
        row = self._conn.execute(
            "SELECT * FROM products WHERE api_key_hash = ?", (hash_key(api_key),)
        ).fetchone()
        if row is None:
            raise ProductNotFound("invalid API key")
        return _to_product(row)

    def list_products(self) -> list[Product]:
        rows = self._conn.execute(
            "SELECT * FROM products ORDER BY created_at ASC"
        ).fetchall()
        return [_to_product(r) for r in rows]

    def rotate_api_key(self, product_id: str) -> str:
        """Issue a new API key for an existing product (plaintext shown once)."""
        self.get(product_id)
        api_key = f"nav_{secrets.token_urlsafe(32)}"
        self._conn.execute(
            "UPDATE products SET api_key_hash = ? WHERE product_id = ?",
            (hash_key(api_key), product_id),
        )
        return api_key

    def products(self) -> list[Product]:
        rows = self._conn.execute(
            "SELECT * FROM products ORDER BY created_at"
        ).fetchall()
        return [_to_product(r) for r in rows]

    # -- site graph revisions -------------------------------------------------

    def put_site_graph(
        self,
        product_id: str,
        yaml_text: str,
        source: SiteGraphSource = "yaml",
        *,
        publish: bool,
    ) -> SiteGraphRevision:
        """Validate and store a new revision, publishing it only if asked.

        `publish` is required and has no default on purpose. An unpublished
        revision is a draft: the Client can test it from their dashboard, but End
        Users on the Client's landing page keep getting the published revision
        until the Client explicitly publishes. Saving an edit must never change
        what live visitors see -- see docs/PRODUCT_MODEL.md.

        Validation happens before anything is written, so a rejected upload leaves
        the active revision untouched -- a customer cannot break a live demo with a
        bad push. Raises SiteGraphError with the same messages the file loader
        produces; there is deliberately only one validator in the system.
        """
        self.get(product_id)  # 404 before doing any work
        graph = parse_site_graph(yaml_text, origin=f"product {product_id}")

        now = datetime.now(timezone.utc)
        revision = (
            self._conn.execute(
                "SELECT COALESCE(MAX(revision), 0) + 1 AS next "
                "FROM site_graph_revisions WHERE product_id = ?",
                (product_id,),
            ).fetchone()["next"]
        )

        self._conn.execute(
            "INSERT INTO site_graph_revisions (product_id, revision, source, yaml, "
            "created_at, site, graph_version, published) VALUES (?,?,?,?,?,?,?,?)",
            (
                product_id,
                revision,
                source,
                yaml_text,
                now.isoformat(),
                graph.site,
                graph.version,
                int(publish),
            ),
        )
        if publish:
            self._conn.execute(
                "UPDATE products SET active_revision = ? WHERE product_id = ?",
                (revision, product_id),
            )
        return SiteGraphRevision(
            product_id=product_id,
            revision=revision,
            source=source,
            yaml=yaml_text,
            created_at=now,
            site=graph.site,
            graph_version=graph.version,
            published=publish,
        )

    def latest_revision(self, product_id: str) -> SiteGraphRevision:
        """The newest revision, published or not -- what the Client is editing."""
        row = self._conn.execute(
            "SELECT * FROM site_graph_revisions WHERE product_id = ? "
            "ORDER BY revision DESC LIMIT 1",
            (product_id,),
        ).fetchone()
        if row is None:
            raise ProductNotFound(f"product {product_id!r} has no site graph yet")
        return SiteGraphRevision.model_validate(dict(row))

    def published_revision(self, product_id: str) -> int:
        """The revision a live demo must run, or raise if nothing is published."""
        revision = self.get(product_id).active_revision
        if revision is None:
            raise ProductNotFound(
                f"product {product_id!r} has no published site graph -- "
                "publish a revision before running live demos"
            )
        return revision

    def get_revision(
        self, product_id: str, revision: int | None = None
    ) -> SiteGraphRevision:
        """A specific revision, or the active one."""
        if revision is None:
            revision = self.get(product_id).active_revision
            if revision is None:
                raise ProductNotFound(
                    f"product {product_id!r} has no site graph yet"
                )
        row = self._conn.execute(
            "SELECT * FROM site_graph_revisions WHERE product_id = ? AND revision = ?",
            (product_id, revision),
        ).fetchone()
        if row is None:
            raise ProductNotFound(
                f"product {product_id!r} has no revision {revision}"
            )
        return SiteGraphRevision.model_validate(dict(row))

    def revisions(self, product_id: str) -> list[SiteGraphRevision]:
        rows = self._conn.execute(
            "SELECT * FROM site_graph_revisions WHERE product_id = ? "
            "ORDER BY revision",
            (product_id,),
        ).fetchall()
        return [SiteGraphRevision.model_validate(dict(r)) for r in rows]

    def load_graph(self, product_id: str, revision: int | None = None) -> SiteGraph:
        """The parsed, validated graph a demo should run against."""
        return parse_site_graph(
            self.get_revision(product_id, revision).yaml,
            origin=f"product {product_id}",
        )

    def activate(self, product_id: str, revision: int) -> Product:
        """Publish a revision: the go-live path for a draft, and the rollback path.

        This is the only way a revision becomes visible to End Users.
        """
        self.get_revision(product_id, revision)
        self._conn.execute(
            "UPDATE site_graph_revisions SET published = 1 "
            "WHERE product_id = ? AND revision = ?",
            (product_id, revision),
        )
        self._conn.execute(
            "UPDATE products SET active_revision = ? WHERE product_id = ?",
            (revision, product_id),
        )
        return self.get(product_id)

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def __enter__(self) -> Registry:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _to_product(row: sqlite3.Row) -> Product:
    return Product(
        product_id=row["product_id"],
        name=row["name"],
        created_at=row["created_at"],
        active_revision=row["active_revision"],
    )
