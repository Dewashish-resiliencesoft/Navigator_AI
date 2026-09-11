"""Load Atlas / Cloudinary sidecar env files into os.environ (never commit those files)."""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SIDECARS = ("atlas-credentials.env", "cloudinary.env", "navigator-cloud.env")


def load_cloud_env(*, root: Path | None = None) -> list[Path]:
    """Parse KEY=VALUE sidecars into os.environ if not already set. Returns loaded paths."""
    base = root or _ROOT
    loaded: list[Path] = []
    for name in _SIDECARS:
        path = base / name
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
        loaded.append(path)
    return loaded
