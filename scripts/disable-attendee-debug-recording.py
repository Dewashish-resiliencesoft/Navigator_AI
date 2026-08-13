#!/usr/bin/env python3
"""Disable Attendee's forced ffmpeg debug rec for Google Meet bots.

Upstream Attendee ``create_debug_recording()`` always returns True for Meet /
Teams / Zoom-web AUDIO_AND_VIDEO. That starts ``ffmpeg x11grab`` (~90% CPU)
and starves live voice. Navigator only wants recording when
``SAVE_DEBUG_RECORDINGS=true`` or bot ``debug_settings.create_debug_recording``.

Usage: disable-attendee-debug-recording.py /path/to/attendee
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_MARKER = "Temporarily enabling this for all google meet meetings"
_NEW = '''    def create_debug_recording(self):
        # ffmpeg x11grab debug rec ~90% CPU and starves live Meet voice.
        # Only record when explicitly requested via env or bot debug_settings.
        if os.getenv("SAVE_DEBUG_RECORDINGS", "false").lower() == "true":
            return True

        debug_settings = self.settings.get("debug_settings", {})
        if debug_settings is None:
            debug_settings = {}
        return debug_settings.get("create_debug_recording", False)
'''
_FN_RE = re.compile(
    r"    def create_debug_recording\(self\):.*?"
    r"        return debug_settings\.get\(\"create_debug_recording\", False\)\n",
    re.DOTALL,
)


def patch(attendee_dir: Path) -> str:
    path = attendee_dir / "bots" / "models.py"
    if not path.is_file():
        return f"missing {path}"
    text = path.read_text(encoding="utf-8")
    if _MARKER not in text and "starves live Meet voice" in text:
        return "already patched"
    if _MARKER not in text:
        return "no forced Meet debug rec block (nothing to patch)"
    new_text, n = _FN_RE.subn(_NEW, text, count=1)
    if n != 1:
        return "create_debug_recording() shape changed; patch skipped"
    path.write_text(new_text, encoding="utf-8")
    return f"patched {path}"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: disable-attendee-debug-recording.py /path/to/attendee", file=sys.stderr)
        return 2
    print(f"[attendee] debug-rec: {patch(Path(sys.argv[1]).expanduser())}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
