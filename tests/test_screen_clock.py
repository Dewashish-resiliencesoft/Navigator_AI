"""Capture page switches /screen.ws on the first byte only.

- A  legacy audio:     uint32le rate | PCM, PCM at offset 5
- F  legacy video:     JPEG at offset 1
- a  timestamped audio: uint32le rate | uint64le ns | PCM, PCM at offset 13
- f  timestamped video: uint64le ns | JPEG at offset 9
- C  barge-in:         drop scheduled PCM and reset the play clock
"""

from __future__ import annotations

from navigator.meeting.screen_page import (
    LEGACY_AUDIO_HEADER,
    STAMPED_AUDIO_HEADER,
    STAMPED_FRAME_HEADER,
    ScreenClockReader,
    parse_screen_payload,
    view_page_html,
)


def _legacy_audio(rate: int, pcm: bytes) -> bytes:
    return b"A" + int(rate).to_bytes(4, "little") + pcm


def _stamped_audio(rate: int, ns: int, pcm: bytes) -> bytes:
    return b"a" + int(rate).to_bytes(4, "little") + int(ns).to_bytes(8, "little") + pcm


def _legacy_frame(jpeg: bytes) -> bytes:
    return b"F" + jpeg


def _stamped_frame(ns: int, jpeg: bytes) -> bytes:
    return b"f" + int(ns).to_bytes(8, "little") + jpeg


def test_five_tags_use_the_agreed_offsets():
    assert LEGACY_AUDIO_HEADER == 5
    assert STAMPED_AUDIO_HEADER == 13
    assert STAMPED_FRAME_HEADER == 9
    ns = 0x0102030405060708
    pcm = b"\xaa\xbb"
    jpeg = b"\xff\xd8\xff\x00"

    legacy_audio = parse_screen_payload(_legacy_audio(16000, pcm))
    assert legacy_audio.kind == "audio"
    assert legacy_audio.sample_rate == 16000
    assert legacy_audio.monotonic_ns is None
    assert legacy_audio.body == pcm

    stamped_audio = parse_screen_payload(_stamped_audio(24000, ns, pcm))
    assert stamped_audio.sample_rate == 24000
    assert stamped_audio.monotonic_ns == ns
    assert stamped_audio.body == pcm
    raw_a = _stamped_audio(24000, ns, pcm)
    assert raw_a[0:1] == b"a"
    assert int.from_bytes(raw_a[1:5], "little") == 24000
    assert int.from_bytes(raw_a[5:13], "little") == ns
    assert raw_a[13:] == pcm

    legacy_frame = parse_screen_payload(_legacy_frame(jpeg))
    assert legacy_frame.kind == "video"
    assert legacy_frame.monotonic_ns is None
    assert legacy_frame.body == jpeg

    stamped_frame = parse_screen_payload(_stamped_frame(ns, jpeg))
    assert stamped_frame.monotonic_ns == ns
    assert stamped_frame.body == jpeg
    raw_f = _stamped_frame(ns, jpeg)
    assert raw_f[0:1] == b"f"
    assert int.from_bytes(raw_f[1:9], "little") == ns
    assert raw_f[9:] == jpeg

    assert parse_screen_payload(b"C").kind == "clear"


def test_uppercase_a_does_not_eat_a_stamp_and_lowercase_is_not_guessed():
    """The same bytes after the rate are PCM for 'A' and a stamp for 'a'."""
    ns = 5_000_000_000
    pcm = b"\x01\x00\x02\x00"
    stamped = _stamped_audio(24000, ns, pcm)
    # Force the legacy tag onto a timestamped body. Offset 5 is PCM, not a stamp.
    as_legacy = b"A" + stamped[1:]
    legacy = parse_screen_payload(as_legacy)
    assert legacy.monotonic_ns is None
    assert legacy.body == stamped[5:]
    assert legacy.body != pcm

    # A raw JPEG is not a frame. Only 'F' and 'f' are.
    assert parse_screen_payload(b"\xff\xd8\xff\x00").kind == "ignore"
    # 'a' is parsed immediately, before any video frame.
    early = parse_screen_payload(_stamped_audio(8000, ns, pcm))
    assert early.monotonic_ns == ns and early.body == pcm


def test_clear_drops_scheduled_pcm_and_resets_the_play_clock():
    reader = ScreenClockReader()
    frame_ns = 5_000_000_000
    video = reader.feed(_stamped_frame(frame_ns, b"\xff\xd8\xff"))
    assert video.monotonic_ns == frame_ns
    assert reader.clock_ns == frame_ns
    reader.feed(_legacy_audio(16000, b"\x01\x00" * 160))
    reader.feed(_stamped_audio(24000, frame_ns + 20_000_000, b"\x02\x00" * 80))
    assert len(reader.scheduled) == 2
    assert reader.play_offset_s > 0
    cleared = reader.feed(b"C")
    assert cleared.kind == "clear"
    assert reader.scheduled == []
    assert reader.play_offset_s == 0.0
    assert reader.clock_ns is None
    # Speech after barge-in is a new clock, not the dropped chunks.
    nxt = reader.feed(_legacy_audio(16000, b"\x03\x00"))
    assert nxt.body == b"\x03\x00"
    assert len(reader.scheduled) == 1


def test_odd_pcm_tail_is_dropped_for_both_audio_tags():
    legacy = parse_screen_payload(_legacy_audio(8000, b"\x01\x00\xff"))
    assert legacy.body == b"\x01\x00"
    stamped = parse_screen_payload(_stamped_audio(8000, 99, b"\x01\x00\xff"))
    assert stamped.monotonic_ns == 99 and stamped.body == b"\x01\x00"


def test_view_page_switches_on_the_five_tags_and_stops_sources_on_clear():
    html = view_page_html(jpeg=False)
    assert "kind === 65" in html  # 'A'
    assert "kind === 70" in html  # 'F'
    assert "kind === 97" in html  # 'a'
    assert "kind === 102" in html  # 'f'
    assert "kind === 67" in html  # 'C'
    assert "evenPcm(u, 5)" in html
    assert "evenPcm(u, 13)" in html
    assert "u.subarray(1)" in html
    assert "u.subarray(9)" in html
    assert "src.stop(0)" in html
    assert "bargeIn" in html
    assert "0xD8" not in html
