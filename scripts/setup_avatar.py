"""Download and verify a GLB avatar with ARKit morph targets.

Run once: python scripts/setup_avatar.py
The GLB is placed at assets/avatar/navigator_avatar.glb.
"""

from pathlib import Path
import urllib.request
import sys

AVATAR_DIR = Path(__file__).resolve().parent.parent / "assets" / "avatar"
GLB_PATH = AVATAR_DIR / "navigator_avatar.glb"

# A neutral, professional half-body avatar.
# Replace this URL with your own avatar source.
# Alternatives: Avaturn, VRoid→GLB, Sketchfab (ARKit tagged).
AVATAR_URLS = [
    # Primary: TalkingHead sample avatar (MIT licensed, ARKit morph targets)
    "https://models.readyplayer.me/64bfa15f0e72c63d7c3934a6.glb?morphTargets=ARKit&textureAtlas=1024",
]


def main() -> int:
    if GLB_PATH.exists():
        size = GLB_PATH.stat().st_size
        print(f"Avatar already exists: {GLB_PATH} ({size:,} bytes)")
        return 0

    AVATAR_DIR.mkdir(parents=True, exist_ok=True)

    for url in AVATAR_URLS:
        print(f"Downloading avatar from {url}…")
        try:
            urllib.request.urlretrieve(url, GLB_PATH)
            size = GLB_PATH.stat().st_size
            if size < 1000:
                print(f"  Too small ({size} bytes) — trying next source")
                GLB_PATH.unlink(missing_ok=True)
                continue
            print(f"  Saved: {GLB_PATH} ({size:,} bytes)")
            return 0
        except Exception as exc:
            print(f"  Failed: {exc}")
            GLB_PATH.unlink(missing_ok=True)
            continue

    print(
        "\nCould not download an avatar. Please manually place a GLB file with "
        "ARKit morph targets at:\n"
        f"  {GLB_PATH}\n\n"
        "Sources:\n"
        "  - https://avaturn.me (generate from selfie)\n"
        "  - https://vroid.com (stylized, convert VRM→GLB)\n"
        "  - Sketchfab: search 'ARKit rigged avatar'\n\n"
        "The avatar will fall back to CSS animation if no GLB is present."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
