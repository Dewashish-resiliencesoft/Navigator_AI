"""Patch Attendee so the audio WS opens before mixed inbound exists.

Zoom's web SDK only emits mixed PCM after recording permission. Attendee used
that first chunk as the trigger to connect — so bot_output had nowhere to go
until someone spoke. Eager-connect the mixed-audio client at manager init.
"""

from __future__ import annotations

from pathlib import Path

_MARKER = "eager-connect mixed audio WS before first chunk"
_NEEDLE = "        self._clients = list(client_by_url.values())\n"


def patch(attendee_dir: Path) -> str:
    path = (
        attendee_dir
        / "bots"
        / "bot_controller"
        / "bot_websocket_client_manager.py"
    )
    if not path.is_file():
        return f"missing {path}"
    text = path.read_text(encoding="utf-8")
    if _MARKER in text:
        return "already patched"
    if _NEEDLE not in text:
        return "BotWebsocketClientManager shape changed; patch skipped"
    insert = (
        "        self._clients = list(client_by_url.values())\n"
        f"        # {_MARKER}\n"
        "        for _client in self._clients:\n"
        "            self._ensure_started(_client)\n"
    )
    path.write_text(text.replace(_NEEDLE, insert, 1), encoding="utf-8")
    return f"patched {path}"


_UPS_MARKER = "navigator: return bytes when rates match"
_UPS_NEEDLE = """        if sample_rate == self.output_sample_rate:
            return chunk, sample_rate
"""


def patch_upsample(attendee_dir: Path) -> str:
    """Attendee bug: same-rate chunk returned as a tuple → TypeError, no audio."""
    path = attendee_dir / "bots" / "bot_controller" / "realtime_audio_output_manager.py"
    if not path.is_file():
        return f"missing {path}"
    text = path.read_text(encoding="utf-8")
    if _UPS_MARKER in text:
        return "already patched"
    if _UPS_NEEDLE not in text:
        return "upsample shape changed; patch skipped"
    fixed = (
        "        if sample_rate == self.output_sample_rate:\n"
        f"            return chunk  # {_UPS_MARKER}\n"
    )
    path.write_text(text.replace(_UPS_NEEDLE, fixed, 1), encoding="utf-8")
    return f"patched {path}"
