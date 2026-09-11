"""Cloudinary uploads for Client assets + demo screenshots."""

from __future__ import annotations

import os
from typing import Any, BinaryIO

from navigator.storage.env_load import load_cloud_env


def _configure() -> None:
    load_cloud_env()
    import cloudinary

    cloudinary.config(
        cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
        api_key=os.environ.get("CLOUDINARY_API_KEY", ""),
        api_secret=os.environ.get("CLOUDINARY_API_SECRET", ""),
        secure=True,
    )
    if not os.environ.get("CLOUDINARY_CLOUD_NAME"):
        raise RuntimeError("CLOUDINARY_CLOUD_NAME not set (cloudinary.env)")


def cloudinary_enabled() -> bool:
    load_cloud_env()
    return bool(os.environ.get("CLOUDINARY_CLOUD_NAME") and os.environ.get("CLOUDINARY_API_SECRET"))


def upload_image(
    file: bytes | BinaryIO,
    *,
    product_id: str,
    folder: str = "uploads",
    public_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Upload bytes/file to Cloudinary under ``navigator-ai/{product_id}/{folder}``."""
    _configure()
    import cloudinary.uploader

    folder_path = f"navigator-ai/{product_id}/{folder}".strip("/")
    result = cloudinary.uploader.upload(
        file,
        folder=folder_path,
        public_id=public_id,
        overwrite=True,
        resource_type="image",
        tags=tags or [product_id, folder],
    )
    return {
        "public_id": result.get("public_id", ""),
        "url": result.get("secure_url") or result.get("url") or "",
        "bytes": int(result.get("bytes") or 0),
        "format": result.get("format") or "",
        "width": result.get("width"),
        "height": result.get("height"),
        "raw": result,
    }


def destroy_image(public_id: str) -> None:
    _configure()
    import cloudinary.uploader

    cloudinary.uploader.destroy(public_id, resource_type="image")
