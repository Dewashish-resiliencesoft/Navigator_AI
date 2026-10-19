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
                     f"currentTime + 0.25; // {_JIT_MARKER}", text)
        if new == text:
            return "already patched"
        path.write_text(new, encoding="utf-8")
        return f"jitter buffer -> 250ms in {path}"
    if _JIT_NEEDLE not in text:
        return "player shape changed; patch skipped"
    fixed = f"            this.nextPlayTime = currentTime + 0.25; // {_JIT_MARKER}\n"
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


_OVR = Path(__file__).with_name("attendee_overrides")
_PUSH_MARKER = "navigator: push scheduler v2"
_PACE_MARKER = "navigator: deadline pacing"
_PACE3_MARKER = "navigator: pacing v3"  # 1.0s lead, 200ms chunks (round 12)


def patch_audio_v2(attendee_dir: Path) -> str:
    """Voice glitches in Zoom (122 underruns x ~460ms in one demo).

    1. Bot-page player was driven by setTimeout, which Chrome throttles in the
       bot tab -> timers fired ~0.5-1s late while 300-400ms was still queued.
       Now each chunk is scheduled on arrival (push scheduler).
    2. Attendee paced output at 0.9x chunk + chromedriver cost (<= real time)
       and dropped the sub-100ms residue after every >150ms Gemini pause
       (lost 16% of audio in the loopback test). Now deadline pacing with
       0.5s lead, residue flushed instead of dropped.
    """
    js = attendee_dir / "bots" / "web_bot_adapter" / "shared_chromedriver_payload.js"
    py = attendee_dir / "bots" / "bot_controller" / "realtime_audio_output_manager.py"
    if not js.is_file() or not py.is_file():
        return "attendee files missing; skipped"
    done = []
    text = js.read_text(encoding="utf-8")
    if _PUSH_MARKER not in text:
        a = text.find("    _processAudioQueue() {")
        b = text.find("    getAudioContextDestination() {")
        if a < 0 or b < a:
            return "player shape changed; patch skipped"
        text = text[:a] + (_OVR / "push_player.js").read_text(encoding="utf-8") + text[b:]
        js.write_text(text, encoding="utf-8")
        done.append("player")
    if _PACE3_MARKER not in py.read_text(encoding="utf-8"):
        py.write_text((_OVR / "realtime_audio_output_manager.py").read_text(encoding="utf-8"), encoding="utf-8")
        done.append("pacing")
    return f"patched {', '.join(done)}" if done else "already patched"


_DRAW2_MARKER = "navigator: frame-driven draw v2"
_FLAGS_MARKER = "navigator: no background throttling"
_FLAGS_NEEDLE = '        options.add_argument("--disable-application-cache")\n'
_FLAGS = (
    f'        # {_FLAGS_MARKER}\n'
    '        for _nav_flag in ("--disable-background-timer-throttling",\n'
    '                          "--disable-renderer-backgrounding",\n'
    '                          "--disable-backgrounding-occluded-windows"):\n'
    '            options.add_argument(_nav_flag)\n'
)


def patch_video_v2(attendee_dir: Path) -> str:
    """Screenshare smoothness in the bot and streamer Chromes.

    - Bot draw loop: draw each decoded share frame once via
      requestVideoFrameCallback (old rAF loop drew 30/s for a 20fps source:
      a third were duplicates, plus up to a vsync of extra delay). Logs
      [bot-video] draw_fps every 10s to the worker log.
    - Bot + webpage-streamer Chrome: disable timer/renderer background
      throttling so timers and painting are never slowed.
    """
    js = attendee_dir / "bots" / "web_bot_adapter" / "shared_chromedriver_payload.js"
    done = []
    if js.is_file():
        text = js.read_text(encoding="utf-8")
        if _DRAW2_MARKER not in text:
            a = text.find("    _startVideoDrawingLoop() {")
            b = text.find("    _stopVideoPlayback() {")
            if 0 <= a < b:
                text = text[:a] + (_OVR / "draw_loop.js").read_text(encoding="utf-8") + text[b:]
                text = text.replace(
                    "        if (this.videoRafId != null) {\n            cancelAnimationFrame(this.videoRafId);",
                    "        if (this._navKeepalive) { clearInterval(this._navKeepalive); this._navKeepalive = null; }\n"
                    "        if (this.videoRafId != null) {\n            if (this.videoRafId > 0) cancelAnimationFrame(this.videoRafId);",
                    1,
                )
                js.write_text(text, encoding="utf-8")
                done.append("draw")
    for rel in (("web_bot_adapter", "web_bot_adapter.py"), ("webpage_streamer", "webpage_streamer.py")):
        f = attendee_dir / "bots" / rel[0] / rel[1]
        if not f.is_file():
            continue
        t = f.read_text(encoding="utf-8")
        if _FLAGS_MARKER in t or _FLAGS_NEEDLE not in t:
            continue
        f.write_text(t.replace(_FLAGS_NEEDLE, _FLAGS_NEEDLE + _FLAGS, 1), encoding="utf-8")
        done.append(rel[0])
    return f"patched {', '.join(done)}" if done else "already patched"
