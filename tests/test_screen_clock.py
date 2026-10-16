"""Capture page accepts legacy /screen.ws frames and the timestamped layout.

Timestamped headers match navigator/meeting/media_clock.py on PR #35:

- audio: A | uint32le sample_rate | uint64le monotonic_ns | int16le PCM (13-byte header)
- video: F | uint64le monotonic_ns | JPEG (9-byte header)

Legacy headers are what this branch sends today:

- audio: A | uint32le sample_rate | PCM (5-byte header)
- video: F | JPEG, or a raw JPEG with no tag
"""

from __future__ import annotations

from navigator.meeting.screen_page import (
    LEGACY_AUDIO_HEADER,
    STAMPED_AUDIO_HEADER,
    STAMPED_FRAME_HEADER,
    SYNC_LATE_S,
    ScreenClockReader,
    parse_screen_payload,
    presentation_delay_s,
    view_page_html,
)


def _audio(rate: int, pcm: bytes, ns: int | None = None) -> bytes:
    head = b"A" + int(rate).to_bytes(4, "little")
    if ns is None:
        return head + pcm
    return head + int(ns).to_bytes(8, "little") + pcm


def _frame(jpeg: bytes, ns: int | None = None) -> bytes:
    if ns is None:
        return b"F" + jpeg
    return b"F" + int(ns).to_bytes(8, "little") + jpeg


def test_header_widths_match_the_shared_clock_spec():
    assert LEGACY_AUDIO_HEADER == 5
    assert STAMPED_AUDIO_HEADER == 13
    assert STAMPED_FRAME_HEADER == 9
    ns = 0x0102030405060708
    audio = _audio(24000, b"\xaa\xbb", ns)
    assert audio[0:1] == b"A"
    assert int.from_bytes(audio[1:5], "little") == 24000
    assert int.from_bytes(audio[5:13], "little") == ns
    assert audio[13:] == b"\xaa\xbb"
    frame = _frame(b"\xff\xd8\xff", ns)
    assert frame[0:1] == b"F"
    assert int.from_bytes(frame[1:9], "little") == ns
    assert frame[9:] == b"\xff\xd8\xff"


def test_legacy_audio_and_video_round_trip():
    pcm = b"\x01\x00\x02\x00"
    jpeg = b"\xff\xd8\xff\x00"
    reader = ScreenClockReader()
    assert reader.feed(_audio(16000, pcm)) == []
    events = reader.feed(_frame(jpeg))
    assert len(events) == 2
    video, audio = events
    assert video.kind == "video" and video.monotonic_ns is None and video.body == jpeg
    assert audio.kind == "audio"
    assert audio.sample_rate == 16000 and audio.monotonic_ns is None and audio.body == pcm
    assert reader.stamped is False


def test_timestamped_audio_and_video_keep_their_stamps():
    pcm = b"\x01\x00\x02\x00"
    jpeg = b"\xff\xd8\xff\x00"
    frame_ns = 5_000_000_000
    audio_ns = frame_ns + 50_000_000
    reader = ScreenClockReader()
    assert reader.feed(_audio(24000, pcm, audio_ns)) == []
    video, audio = reader.feed(_frame(jpeg, frame_ns))
    assert video.monotonic_ns == frame_ns and video.body == jpeg
    assert audio.sample_rate == 24000
    assert audio.monotonic_ns == audio_ns and audio.body == pcm
    assert reader.stamped is True
    assert reader.clock_ns == frame_ns
    # Later chunks on the same socket stay on the stamped header.
    later = reader.feed(_audio(24000, b"\x03\x00", audio_ns + 20_000_000))
    assert later[0].monotonic_ns == audio_ns + 20_000_000
    assert later[0].body == b"\x03\x00"


def test_untagged_jpeg_and_non_jpeg_frame_stay_legacy():
    raw = parse_screen_payload(b"\xff\xd8\xff\x00")
    assert raw.kind == "video" and raw.monotonic_ns is None
    assert raw.body == b"\xff\xd8\xff\x00"
    # This branch's relay sends F + opaque bytes, not a JPEG SOI.
    opaque = parse_screen_payload(b"FNEW")
    assert opaque.kind == "video" and opaque.monotonic_ns is None and opaque.body == b"NEW"


def test_clear_drops_audio_queued_before_the_first_frame():
    reader = ScreenClockReader()
    reader.feed(_audio(8000, b"\x01\x00"))
    cleared = reader.feed(b"C")
    assert [m.kind for m in cleared] == ["clear"]
    video = reader.feed(_frame(b"\xff\xd8\xff"))
    assert len(video) == 1 and video[0].kind == "video"


def test_odd_pcm_tail_is_dropped_in_both_layouts():
    legacy = parse_screen_payload(b"A" + (8000).to_bytes(4, "little") + b"\x01\x00\xff", stamped=False)
    assert legacy.body == b"\x01\x00"
    stamped = parse_screen_payload(
        _audio(8000, b"\x01\x00\xff", 99),
        stamped=True,
    )
    assert stamped.monotonic_ns == 99 and stamped.body == b"\x01\x00"


def test_presentation_delay_uses_the_shared_stamp():
    anchor = 5_000_000_000
    assert presentation_delay_s(anchor + 50_000_000, anchor) == 0.05
    assert presentation_delay_s(anchor - 10_000_000, anchor) == -0.01
    assert SYNC_LATE_S == 0.05


def test_view_page_parses_both_layouts_and_schedules_on_the_stamp():
    html = view_page_html(jpeg=False)
    assert "subarray(13" in html  # stamped PCM after rate + uint64
    assert "subarray(5" in html  # legacy PCM after rate
    assert "subarray(9)" in html  # stamped JPEG after uint64
    assert "0xD8" in html
    assert "(ns - clockNs) / 1e9" in html
    assert "onPayload" in html
