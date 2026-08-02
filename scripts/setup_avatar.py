"""Download or verify the meeting avatar GLB (ARKit morphs preferred).

Preferred path: assets/avatar/female_avatar.glb
Legacy fallback: assets/avatar/navigator_avatar.glb

Run: python scripts/setup_avatar.py

Ready Player Me CDN often blocked — default mirrors use GitHub/jsDelivr
(TalkingHead brunette sample, MIT-friendly demo asset with ARKit morphs).
"""

from __future__ import annotations

import struct
import sys
import urllib.request
from pathlib import Path

AVATAR_DIR = Path(__file__).resolve().parent.parent / "assets" / "avatar"
PREFERRED = AVATAR_DIR / "female_avatar.glb"
LEGACY = AVATAR_DIR / "navigator_avatar.glb"

# Free ARKit-morph female samples (no Ready Player Me DNS required).
AVATAR_URLS = [
    # TalkingHead demo brunette (~2.8MB, 268 ARKit morphs incl. jawOpen / visemes)
    "https://raw.githubusercontent.com/met4citizen/TalkingHead/main/avatars/brunette-t.glb",
    "https://cdn.jsdelivr.net/gh/met4citizen/TalkingHead@main/avatars/brunette-t.glb",
]


def _has_arkit_morphs(path: Path) -> bool:
    data = path.read_bytes()
    if b"jawOpen" in data and b"eyeBlinkLeft" in data:
        return True
    # JSON chunk scan for visemes
    if len(data) < 20:
        return False
    try:
        chunk_len = struct.unpack_from("<I", data, 12)[0]
        raw = data[20 : 20 + chunk_len]
        return b"viseme_aa" in raw or b"jawOpen" in raw
    except Exception:
        return False


def main() -> int:
    for path in (PREFERRED, LEGACY):
        if path.is_file() and path.stat().st_size > 1000:
            print(f"Avatar ready: {path} ({path.stat().st_size:,} bytes)")
            if _has_arkit_morphs(path):
                print("  ARKit morphs: yes (lip sync OK)")
            else:
                print(
                    "  ARKit morphs: no — 3D shows, lip sync off. "
                    "Re-run with --force to download TalkingHead sample."
                )
            if "--force" not in sys.argv:
                return 0
            break

    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    for url in AVATAR_URLS:
        print(f"Downloading ARKit avatar from {url}…")
        try:
            urllib.request.urlretrieve(url, PREFERRED)
            size = PREFERRED.stat().st_size
            if size < 1000:
                print(f"  Too small ({size} bytes) — trying next")
                PREFERRED.unlink(missing_ok=True)
                continue
            if not _has_arkit_morphs(PREFERRED):
                print("  Downloaded but no ARKit morphs — trying next")
                PREFERRED.unlink(missing_ok=True)
                continue
            print(f"  Saved: {PREFERRED} ({size:,} bytes) — ARKit morphs OK")
            return 0
        except Exception as exc:
            print(f"  Failed: {exc}")
            PREFERRED.unlink(missing_ok=True)
            continue

    print(
        f"\nCould not download. Place an ARKit GLB at:\n  {PREFERRED}\n\n"
        "Mirrors that work without readyplayer.me:\n"
        "  https://github.com/met4citizen/TalkingHead/tree/main/avatars\n"
        "  (brunette-t.glb / brunette.glb / avaturn.glb)\n"
        "Or Avaturn / VRoid with blendshapes exported to GLB."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
