"""Image metadata in Mongo + bytes in Cloudinary."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, BinaryIO

from navigator.storage.media import cloudinary_enabled, destroy_image, upload_image
from navigator.storage.mongo import get_db, mongo_enabled


class ImageStore:
    def save(
        self,
        *,
        product_id: str,
        file: bytes | BinaryIO,
        kind: str = "upload",
        demo_id: str = "",
        filename: str = "",
    ) -> dict[str, Any]:
        if not mongo_enabled() or not cloudinary_enabled():
            raise RuntimeError("Mongo + Cloudinary required for image storage")
        public_id = f"{product_id}_{kind}_{secrets.token_hex(8)}"
        up = upload_image(
            file,
            product_id=product_id,
            folder=kind,
            public_id=public_id,
            tags=[product_id, kind],
        )
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "image_id": f"img_{secrets.token_urlsafe(12)}",
            "product_id": product_id,
            "demo_id": demo_id,
            "kind": kind,
            "filename": filename,
            "public_id": up["public_id"],
            "url": up["url"],
            "bytes": up["bytes"],
            "format": up["format"],
            "width": up.get("width"),
            "height": up.get("height"),
            "created_at": now,
        }
        get_db().images.insert_one(doc)
        doc.pop("_id", None)
        return {k: v for k, v in doc.items() if k != "_id"}

    def list_for_product(self, product_id: str, *, limit: int = 50) -> list[dict]:
        cur = (
            get_db()
            .images.find({"product_id": product_id}, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
        return list(cur)

    def delete(self, product_id: str, image_id: str) -> bool:
        doc = get_db().images.find_one(
            {"product_id": product_id, "image_id": image_id}, {"_id": 0}
        )
        if not doc:
            return False
        try:
            destroy_image(doc["public_id"])
        except Exception:  # noqa: BLE001
            pass
        get_db().images.delete_one({"product_id": product_id, "image_id": image_id})
        return True
