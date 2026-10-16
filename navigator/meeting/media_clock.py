"""Shared clock for agent audio and screenshare frames.

``time.monotonic_ns()`` is the only clock. It is process-monotonic: compare
stamps to each other, do not convert them to wall time.

The /view capture page (screenshare video mode) aligns speech to frames with
these binary websocket messages on ``/screen.ws``. The tag byte selects the
layout. ``/view`` does not guess whether a stamp is present.

- Timestamped audio ``a`` | uint32le sample_rate | uint64le monotonic_ns | int16le PCM
- Timestamped video ``f`` | uint64le monotonic_ns | JPEG bytes
- Untimestamped audio ``A`` | uint32le sample_rate | int16le PCM
- Untimestamped video ``F`` | JPEG bytes
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
    """Timestamped capture-page audio. Tag ``a`` so ``/view`` does not guess."""
    stamp = now_ns() if monotonic_ns is None else int(monotonic_ns)
    rate = int(sample_rate)
    if rate < 0:
        rate = 0
    body = pcm or b""
    if len(body) % 2:
        body = body[:-1]
    return b"a" + rate.to_bytes(_U32, "little") + stamp.to_bytes(_U64, "little") + body


def pack_frame(jpeg: bytes, monotonic_ns: int | None = None) -> bytes:
    """Timestamped capture-page video. Tag ``f`` so ``/view`` does not guess."""
    stamp = now_ns() if monotonic_ns is None else int(monotonic_ns)
    return b"f" + stamp.to_bytes(_U64, "little") + (jpeg or b"")


def pack_clear() -> bytes:
    """Barge-in. Drop queued PCM and reset the page play clock."""
    return b"C"


def unpack_audio(payload: bytes) -> tuple[int, int, bytes]:
    """Return ``(sample_rate, monotonic_ns, pcm)``.

    Tag ``a`` carries a stamp. Tag ``A`` is the untimestamped layout and
    returns ``monotonic_ns`` 0. PCM bytes are never read as a stamp.
    """
    if not payload:
        raise ValueError("not an audio clock message")
    tag = payload[:1]
    if tag == b"a":
        if len(payload) < 1 + _U32 + _U64:
            raise ValueError("not an audio clock message")
        rate = int.from_bytes(payload[1:5], "little")
        stamp = int.from_bytes(payload[5:13], "little")
        return rate, stamp, payload[13:]
    if tag == b"A":
        if len(payload) < 1 + _U32:
            raise ValueError("not an audio clock message")
        rate = int.from_bytes(payload[1:5], "little")
        return rate, 0, payload[5:]
    raise ValueError("not an audio clock message")


def unpack_frame(payload: bytes) -> tuple[int, bytes]:
    """Return ``(monotonic_ns, jpeg)``.

    Tag ``f`` carries a stamp. Tag ``F`` is the untimestamped layout and
    returns ``monotonic_ns`` 0. JPEG bytes are never read as a stamp.
    """
    if not payload:
        raise ValueError("not a frame clock message")
    tag = payload[:1]
    if tag == b"f":
        if len(payload) < 1 + _U64:
            raise ValueError("not a frame clock message")
        stamp = int.from_bytes(payload[1:9], "little")
        return stamp, payload[9:]
    if tag == b"F":
        return 0, payload[1:]
    raise ValueError("not a frame clock message")
