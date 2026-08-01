"""Chroma collections, namespaced per product.

Two kinds, kept separate on purpose:

  product_knowledge -- static-ish facts about one product. Written by an ingestion
    endpoint, read at planning time.

  corrections -- one entry per APPROVED corrective rule, tagged with `page` and
    `tool_call_type` metadata so planning retrieves only what's relevant to where
    the agent is and what it's about to do.

Both are namespaced by product_id. A rule learned demoing product A is wrong for
product B and quite possibly confidential, so cross-tenant leakage here is a
correctness bug and a data-protection one at the same time.

Reflection output does not land in `corrections`. It goes to a pending review
table and a human promotes it; an agent that can silently rewrite its own rules is
not debuggable.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

Kind = Literal["product_knowledge", "corrections"]

#: Chroma names must be 3-63 chars, alphanumeric plus _ and -, and start/end
#: alphanumeric. Product ids are already slugs, but the suffix can push a long one
#: over the limit, so the name is built rather than concatenated blindly.
_MAX_NAME = 63
_VALID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,61}[a-zA-Z0-9]$")


def collection_name(product_id: str, kind: Kind) -> str:
    """Namespaced collection name for one product.

    Long product ids are truncated with a hash suffix rather than silently
    colliding -- two customers sharing a collection is the worst failure this
    module can have.
    """
    suffix = "_kb" if kind == "product_knowledge" else "_corr"
    room = _MAX_NAME - len(suffix)
    if len(product_id) <= room:
        name = f"{product_id}{suffix}"
    else:
        import hashlib

        digest = hashlib.sha256(product_id.encode()).hexdigest()[:8]
        name = f"{product_id[: room - 9]}-{digest}{suffix}"

    if not _VALID.match(name):
        raise ValueError(f"cannot build a valid Chroma name from {product_id!r}")
    return name


def get_client(path: str | Path):
    import chromadb

    return chromadb.PersistentClient(path=str(path))


def get_collection(path: str | Path, product_id: str, kind: Kind):
    """One product's collection, created if absent."""
    return get_client(path).get_or_create_collection(
        name=collection_name(product_id, kind)
    )
