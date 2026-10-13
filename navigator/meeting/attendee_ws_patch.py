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


_JIT_MARKER = "navigator: 250ms jitter buffer"
_JIT_NEEDLE = "            this.nextPlayTime = currentTime;\n"


def patch_jitter(attendee_dir: Path) -> str:
    """Bot voice crackle: Attendee's player restarts at currentTime after every
    underrun (zero lookahead) while chunks arrive only ~10% faster than real
    time over chromedriver. Start each burst 400ms ahead so jitter is absorbed.
    """
    path = attendee_dir / "bots" / "web_bot_adapter" / "shared_chromedriver_payload.js"
    if not path.is_file():
        return f"missing {path}"
    text = path.read_text(encoding="utf-8")
    if _JIT_MARKER in text:
        import re

        new = re.sub(r"currentTime \+ [0-9.]+; // " + re.escape(_JIT_MARKER),
                     f"currentTime + 0.4; // {_JIT_MARKER}", text)
        if new == text:
            return "already patched"
        path.write_text(new, encoding="utf-8")
        return f"jitter buffer -> 400ms in {path}"
    if _JIT_NEEDLE not in text:
        return "player shape changed; patch skipped"
    fixed = f"            this.nextPlayTime = currentTime + 0.4; // {_JIT_MARKER}\n"
    path.write_text(text.replace(_JIT_NEEDLE, fixed, 1), encoding="utf-8")
    return f"patched {path}"


_SCHED_MARKER = "navigator: lead scheduler"
_SCHED_NEEDLE = """        const timeUntilNextProcessMs =
            (this.nextPlayTime - currentTime) * 1000 * 0.8;
"""


def patch_scheduler(attendee_dir: Path) -> str:
    """Crackle: Attendee schedules ONE chunk per timer tick at 80% of the
    remaining lead, so the lead collapses to ~1.25 chunk (~110ms for ~90ms
    chunks) and any timer jank underruns. Keep 300-600ms scheduled instead.
    """
    path = attendee_dir / "bots" / "web_bot_adapter" / "shared_chromedriver_payload.js"
    if not path.is_file():
        return f"missing {path}"
    text = path.read_text(encoding="utf-8")
    if _SCHED_MARKER in text:
        return "already patched"
    if _SCHED_NEEDLE not in text:
        return "player shape changed; patch skipped"
    fixed = (
        f"        // {_SCHED_MARKER}\n"
        "        const lead = this.nextPlayTime - currentTime;\n"
        "        const timeUntilNextProcessMs =\n"
        "            (this.audioQueue.length && lead < 0.6) ? 0 : (lead - 0.3) * 1000;\n"
    )
    path.write_text(text.replace(_SCHED_NEEDLE, fixed, 1), encoding="utf-8")
    return f"patched {path}"


_DRAW_MARKER = "navigator: share draw ~30fps"
_DRAW_NEEDLE = "        const drawInterval = 1000 / 15; // ~15fps (60fps / 4)\n"


def patch_drawloop(attendee_dir: Path) -> str:
    """Lag: the bot copies the incoming share video onto its canvas via rAF
    throttled to >=66.7ms. With 16.7ms vsync ticks that lands on every 5th
    tick (83ms, ~12fps) and drops further under Zoom SDK load. Allow ~30fps so
    the 20fps source passes through frame-for-frame.
    """
    path = attendee_dir / "bots" / "web_bot_adapter" / "shared_chromedriver_payload.js"
    if not path.is_file():
        return f"missing {path}"
    text = path.read_text(encoding="utf-8")
    if _DRAW_MARKER in text:
        return "already patched"
    if _DRAW_NEEDLE not in text:
        return "draw loop shape changed; patch skipped"
    fixed = f"        const drawInterval = 1000 / 30 - 4; // {_DRAW_MARKER}\n"
    path.write_text(text.replace(_DRAW_NEEDLE, fixed, 1), encoding="utf-8")
    return f"patched {path}"
