"""Shared clock for agent audio and screenshare frames.

``time.monotonic_ns()`` is the only clock. It is process-monotonic: compare
stamps to each other, do not convert them to wall time.

The /view capture page (screenshare video mode) aligns speech to frames with
these binary websocket messages on ``/screen.ws``:

- Audio ``A`` | uint32le sample_rate | uint64le monotonic_ns | int16le PCM
- Video ``F`` | uint64le monotonic_ns | JPEG bytes
- Clear ``C`` — barge-in. Drop queued PCM and reset the page play clock.

Attendee's bot-output JSON carries the same integer on each chunk:

``{"trigger": "realtime_audio.bot_output", "data": {"chunk", "sample_rate", "monotonic_ns"}}``

``/frame.jpg`` and ``/status`` also expose the latest frame stamp as
``X-Navigator-Monotonic-Ns`` and ``frame_monotonic_ns``.
"""

from __future__ import annotations

import time

_U32 = 4
_U64 = 8


def now_ns() -> int:
    return time.monotonic_ns()


def pack_audio(pcm: bytes, sample_rate: int, monotonic_ns: int | None = None) -> bytes:
    """Capture-page audio message, including the shared stamp."""
    stamp = now_ns() if monotonic_ns is None else int(monotonic_ns)
    rate = int(sample_rate)
    if rate < 0:
        rate = 0
    body = pcm or b""
    if len(body) % 2:
        body = body[:-1]
    return b"A" + rate.to_bytes(_U32, "little") + stamp.to_bytes(_U64, "little") + body


def pack_frame(jpeg: bytes, monotonic_ns: int | None = None) -> bytes:
    """Capture-page video message, including the shared stamp."""
    stamp = now_ns() if monotonic_ns is None else int(monotonic_ns)
    return b"F" + stamp.to_bytes(_U64, "little") + (jpeg or b"")


def unpack_audio(payload: bytes) -> tuple[int, int, bytes]:
    """Return ``(sample_rate, monotonic_ns, pcm)`` from an ``A`` message."""
    if not payload or payload[:1] != b"A" or len(payload) < 1 + _U32 + _U64:
        raise ValueError("not an audio clock message")
    rate = int.from_bytes(payload[1:5], "little")
    stamp = int.from_bytes(payload[5:13], "little")
    return rate, stamp, payload[13:]


def unpack_frame(payload: bytes) -> tuple[int, bytes]:
    """Return ``(monotonic_ns, jpeg)`` from an ``F`` message."""
    if not payload or payload[:1] != b"F" or len(payload) < 1 + _U64:
        raise ValueError("not a frame clock message")
    stamp = int.from_bytes(payload[1:9], "little")
    return stamp, payload[9:]
