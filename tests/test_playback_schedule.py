"""Cue schedule: speak+act overlap, verbatim gaps, TTS stretch keeps sync."""

from __future__ import annotations

from navigator.agent.playback_schedule import Cue, build_schedule, fmt_ms


def _no_tts(_text: str) -> int | None:
    return None


def _at(cues: list[Cue], idx: int, kind: str) -> int:
    return next(c.at_ms for c in cues if c.idx == idx and c.kind == kind)


def _has(cues: list[Cue], idx: int, kind: str) -> bool:
    return any(c.idx == idx and c.kind == kind for c in cues)


def test_narrated_act_starts_with_speak():
    """Cursor/click ride the line — not after the host finished talking."""
    cues, _total = build_schedule(
        n_steps=2,
        clicks={0: 1200, 1: 8000},
        speech={0: (300, 1100), 1: (6400, 7800)},
        lines=["Sign up here.", "Then confirm."],
        timing={},
        tts_ms=_no_tts,
    )
    assert _at(cues, 0, "act") == _at(cues, 0, "speak")
    assert _at(cues, 1, "act") == _at(cues, 1, "speak")


def test_long_silent_gap_replays_verbatim():
    # 30s of the host reading something. Previously clamped to 1.2s.
    cues, total = build_schedule(
        n_steps=2,
        clicks={0: 1000, 1: 31000},
        speech={0: (0, 800)},
        lines=["Here we go.", ""],
        timing={},
        tts_ms=_no_tts,
    )
    assert _at(cues, 0, "act") == _at(cues, 0, "speak") == 0
    assert _at(cues, 1, "act") == 31000
    assert total >= 31000


def test_tts_overflow_shifts_later_cues_and_keeps_speak_act_glued():
    # Line 0's audio runs 5s but the host only spent ~2s on that beat.
    cues, _total = build_schedule(
        n_steps=2,
        clicks={0: 1200, 1: 3000},
        speech={0: (300, 2300), 1: (2100, 2900)},
        lines=["A much longer synthesized line.", "Next."],
        timing={},
        tts_ms=lambda t: 5000 if t.startswith("A much") else 500,
    )
    assert _at(cues, 0, "speak") == 300
    assert _at(cues, 1, "speak") > 2100
    assert _at(cues, 0, "act") == _at(cues, 0, "speak")
    assert _at(cues, 1, "act") == _at(cues, 1, "speak")


def test_silent_step_has_act_cue_only_and_stays_on_time():
    cues, _total = build_schedule(
        n_steps=3,
        clicks={0: 1000, 1: 4000, 2: 9000},
        speech={0: (200, 900), 2: (8000, 8800)},
        lines=["One.", "", "Three."],
        timing={},
        tts_ms=_no_tts,
    )
    assert not _has(cues, 1, "speak")
    assert _has(cues, 1, "act")
    assert _at(cues, 1, "act") == 4000


def test_silent_click_burst_is_spaced_for_watchability():
    # Real recording: 4 clicks in ~2s, steps 1-3 have no narration. Playback
    # must not fire them back-to-back — space the silent ones apart.
    cues, _total = build_schedule(
        n_steps=4,
        clicks={0: 0, 1: 1000, 2: 1500, 3: 2000},
        speech={0: (0, 800)},
        lines=["Intro line.", "", "", ""],
        timing={},
        tts_ms=_no_tts,
    )
    acts = [_at(cues, i, "act") for i in range(4)]
    assert acts[0] == 0
    assert acts[1] - acts[0] >= 2500
    assert acts[2] - acts[1] >= 2500
    assert acts[3] - acts[2] >= 2500


def test_narrated_steps_keep_recorded_pacing_not_spaced():
    # Burst spacer must not invent gaps between narrated steps.
    cues, _total = build_schedule(
        n_steps=2,
        clicks={0: 0, 1: 900},
        speech={0: (0, 400), 1: (600, 850)},
        lines=["One.", "Two."],
        timing={},
        tts_ms=_no_tts,
    )
    assert _at(cues, 1, "speak") == 600
    assert _at(cues, 1, "act") == _at(cues, 1, "speak")


def test_legacy_flow_without_step_speech_falls_back_to_clicks():
    # Every flow recorded before step_speech existed. No invented lead-in, but
    # pacing is honoured rather than clamped.
    cues, _total = build_schedule(
        n_steps=2,
        clicks={0: 500, 1: 20000},
        speech={},
        lines=["One.", "Two."],
        timing={0: 19500, 1: 0},
        tts_ms=_no_tts,
    )
    assert _at(cues, 0, "speak") == _at(cues, 0, "act") == 500
    assert _at(cues, 1, "speak") == _at(cues, 1, "act") == 20000


def test_no_clicks_falls_back_to_cumulative_timing():
    cues, total = build_schedule(
        n_steps=3,
        clicks={},
        speech={},
        lines=["a", "b", "c"],
        timing={0: 2000, 1: 3000, 2: 1000},
        tts_ms=_no_tts,
    )
    assert [_at(cues, i, "act") for i in range(3)] == [0, 2000, 5000]
    assert total == 6000


def test_no_metadata_at_all_uses_default_spacing():
    cues, _total = build_schedule(
        n_steps=3,
        clicks={},
        speech={},
        lines=["a", "b", "c"],
        timing={},
        tts_ms=_no_tts,
    )
    assert [_at(cues, i, "act") for i in range(3)] == [0, 3000, 6000]


def test_unknown_tts_duration_does_not_stretch():
    args = dict(
        n_steps=2,
        clicks={0: 1000, 1: 4000},
        speech={0: (200, 900), 1: (3000, 3800)},
        lines=["One.", "Two."],
        timing={},
    )
    known, _ = build_schedule(**args, tts_ms=lambda t: 700)
    unknown, _ = build_schedule(**args, tts_ms=_no_tts)
    assert [c.at_ms for c in known] == [c.at_ms for c in unknown]


def test_unknown_tts_long_line_stretches_like_measured():
    """Prefetch miss must still push later cues when the line is long."""
    long = " ".join(["word"] * 40)  # ~16s estimate vs 700ms recorded window
    cues, _ = build_schedule(
        n_steps=2,
        clicks={0: 1200, 1: 3000},
        speech={0: (300, 1000), 1: (2100, 2900)},
        lines=[long, "Next."],
        timing={},
        tts_ms=_no_tts,
    )
    assert _at(cues, 1, "speak") > 2100
    assert _at(cues, 1, "act") == _at(cues, 1, "speak")


def test_cues_are_non_decreasing_and_total_covers_last_line():
    cues, total = build_schedule(
        n_steps=3,
        clicks={0: 1000, 1: 5000, 2: 9000},
        speech={0: (0, 900), 1: (4000, 4900), 2: (8000, 8900)},
        lines=["a", "b", "c"],
        timing={},
        tts_ms=lambda _t: 1500,
    )
    times = [c.at_ms for c in cues]
    assert times == sorted(times)
    assert total >= _at(cues, 2, "speak") + 1500


def test_empty_flow():
    assert build_schedule(
        n_steps=0, clicks={}, speech={}, lines=[], timing={}, tts_ms=_no_tts
    ) == ([], 0)


def test_fmt_ms_matches_recorder_widget():
    assert fmt_ms(0) == "00:00"
    assert fmt_ms(12_400) == "00:12"
    assert fmt_ms(61_000) == "01:01"
    assert fmt_ms(-5) == "00:00"
