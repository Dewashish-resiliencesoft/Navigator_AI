"""Barge-in helper: detect continuous user speech / stop-words during TTS."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from queue import Empty, Queue

_STOP = re.compile(
    r"\b(stop|wait|hold on|hold up|pause|listen|excuse me|hang on|one sec|"
    r"one second|sorry|actually)\b",
    re.I,
)


def pcm_rms(pcm: bytes) -> float:
    import array

    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return 0.0
    # Mean absolute amplitude — cheap energy proxy.
    return sum(abs(s) for s in samples) / float(len(samples))


def make_barge_in_checker(
    inbound: Queue,
    *,
    is_bot_echo: Callable[[str], bool] | None = None,
    transcribe: Callable[[bytes], str] | None = None,
    energy_threshold: float = 900.0,
    pending_barge_in: list[str] | None = None,
) -> Callable[[], bool]:
    """Return a checker suitable for MeetSpeaker.check_barge_in.

    Throttles STT: energy must stay high across polls, then one Whisper call.
    """
    state = {"last_stt": 0.0, "hot_streak": 0}

    def check() -> bool:
        chunks: list[bytes] = []
        while True:
            try:
                chunks.append(inbound.get_nowait())
            except Empty:
                break
        if not chunks:
            state["hot_streak"] = 0
            return False
        peak = max(pcm_rms(c) for c in chunks)
        if peak < energy_threshold:
            state["hot_streak"] = 0
            return False
        state["hot_streak"] += 1
        # Need sustained energy (user talking over bot), not a blip.
        if state["hot_streak"] < 3:
            return False
        if transcribe is None:
            return True
        now = time.monotonic()
        if now - state["last_stt"] < 0.7:
            return False
        state["last_stt"] = now
        pcm = b"".join(chunks)
        try:
            text = (transcribe(pcm) or "").strip()
        except Exception as exc:  # noqa: BLE001
            print(f"[barge] stt skipped: {exc}", flush=True)
            return False
        if not text:
            return False
        if is_bot_echo is not None and is_bot_echo(text):
            print(f"[barge] ignore echo: {text!r}", flush=True)
            return False
        continuous = len(text.split()) >= 3
        stop = bool(_STOP.search(text))
        if continuous or stop:
            print(f"[barge] heard: {text!r}", flush=True)
            if pending_barge_in is not None:
                pending_barge_in.clear()
                pending_barge_in.append(text)
            return True
        return False

    return check
