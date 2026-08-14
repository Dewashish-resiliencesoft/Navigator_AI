"""Narrated recording pipeline — transcribe, align, refine, skip."""

from __future__ import annotations

from dataclasses import dataclass

from navigator.automation import narration


@dataclass
class _Step:
    at_ms: int


def test_align_maps_speech_to_clicks():
    segments = [
        narration.Segment(500, 900, "Welcome to contacts"),
        narration.Segment(5500, 5900, "Now I add someone"),
    ]
    lines = narration.align(segments, [0, 5000, 10000])
    assert "Welcome" in lines[0]
    assert "add someone" in lines[1]
    assert lines[2] == ""


def test_strip_fillers_removes_disfluency():
    assert "um" not in narration.strip_fillers("um so like open inbox").lower()
    assert "open inbox" in narration.strip_fillers("um so like open inbox")


def test_spoken_for_live_step_keeps_full_refined_line():
    raw = (
        "yeah so this is the landing page I mean from here you can check "
        "all the things and then you can go and click on start for free"
    )
    out = narration.spoken_for_live_step(raw)
    assert len(out) >= len(raw) - 20
    assert "landing page" in out.lower()
    assert "start for free" in out.lower()


def test_spoken_for_live_step_can_cap_for_planning_fallback():
    raw = "word " * 80
    out = narration.spoken_for_live_step(raw, max_len=200)
    assert len(out) <= 210


def test_refine_returns_input_on_failure():
    lines = ["umm so basically open inbox"]
    assert narration.refine(lines, ask_text=None) == lines


def test_refine_applies_llm_cleanup():
    def ask(_prompt: str) -> str:
        assert "filler" in _prompt.lower() or "grammar" in _prompt.lower()
        return '{"lines": ["Open the inbox."]}'

    out = narration.refine(["umm open inbox"], ask_text=ask)
    assert out == ["Open the inbox."]


def test_refine_rejects_over_shortened_llm_output():
    long_line = (
        "when signing up you'll need to create a phone name whatsapp password "
        "and confirm your password twice"
    )

    def ask(_prompt: str) -> str:
        return '{"lines": ["Sign up here."]}'

    out = narration.refine([long_line], ask_text=ask)
    assert out == [long_line]


def test_translate_lines_when_target_differs():
    def ask(_prompt: str) -> str:
        assert "Hindi" in _prompt
        return '{"lines": ["इनबॉक्स खोलें।"]}'

    out = narration.translate_lines(
        ["Open the inbox."], target="hi", ask_text=ask
    )
    assert "इनबॉक्स" in out[0]


def test_skip_indices_drops_silent_rapid_taps():
    lines = ["hello", "", ""]
    times = [0, 200, 350]
    assert narration.skip_indices(lines, times) == {1, 2}


def test_narrate_recording_end_to_end():
    steps = [_Step(0), _Step(5000), _Step(10000)]
    seen: dict[str, str] = {}

    def transcribe_verbose(**kw):
        seen.update(kw)
        return {
            "segments": [
                {"start": 0.5, "end": 1.5, "text": "First bit"},
                {"start": 5.5, "end": 6.5, "text": "Second bit"},
            ]
        }

    def ask(_prompt: str) -> str:
        return '{"lines": ["First clean.", "Second clean."]}'

    lines, timings, windows = narration.narrate_recording(
        audio=b"fake",
        steps=steps,
        api_key="test",
        ask_text=ask,
        transcribe_verbose=transcribe_verbose,
        language="en",
        translate_to="same",
    )
    assert seen.get("language") == "en"
    assert any("First" in l for l in lines)
    assert any("Second" in l for l in lines)
    assert timings[0]["idx"] == 0
    # Compact clock: gaps follow line length, not the original 5s click spacing.
    assert timings[0]["speak_ms"] < 10_000
    assert windows[0] is not None and windows[1] is not None
    assert windows[2] is None
    assert windows[0][1] - windows[0][0] < 10_000


def test_speech_windows_span_segments_and_skip_silence():
    segs = [
        narration.Segment(500, 1500, "one"),
        narration.Segment(1800, 2400, "still one"),
        narration.Segment(9000, 9900, "three"),
    ]
    # Step 1 (at 5000) gets nothing: seg at 1800+2500 lead-in < 5000, and the
    # 9000 segment belongs to step 2.
    assert narration.speech_windows(segs, [0, 5000, 8000]) == [
        (500, 2400),
        None,
        (9000, 9900),
    ]


def test_speech_windows_payload_omits_silent_steps():
    windows = [(100, 200), None, (900, 1000)]
    assert narration.speech_windows_payload(windows) == [
        {"idx": 0, "start_ms": 100, "end_ms": 200},
        {"idx": 2, "start_ms": 900, "end_ms": 1000},
    ]


def test_align_unchanged_by_assign_extraction():
    segs = [narration.Segment(0, 900, "a"), narration.Segment(1000, 1900, "b")]
    assert narration.align(segs, [0, 6000]) == ["a b", ""]


_MONO = (
    "Welcome to the dashboard where you can see every conversation at a glance. "
    "From here the team tracks replies, tags, and assignments in one place. "
    "Click campaigns to open the list of running outreach. "
    "Then create a new one and pick the audience you want to reach. "
    "Fill in the name and the message template before you continue. "
    "Save when you are done and watch it go live for the team."
)


def test_pace_lines_splits_monologue_onto_silent_steps():
    lines = [_MONO, "", "", "", ""]
    hints = ["dashboard", "campaigns", "create", "name_field", "save"]
    out = narration.pace_lines(lines, hints=hints)
    assert all(l.strip() for l in out)
    assert out[0] != _MONO
    assert all(len(l.split()) <= narration.MAX_WORDS for l in out)
    joined = " ".join(out).lower()
    assert "dashboard" in joined or "welcome" in joined
    assert "campaigns" in joined or "create" in joined


def test_pace_lines_keeps_short_narrated_steps():
    lines = ["Open inbox.", "Compose a message.", "Send it."]
    assert narration.pace_lines(lines) == lines


def test_pace_lines_fills_empty_from_hints():
    out = narration.pace_lines(
        ["Welcome.", "", ""],
        hints=["home", "inbox_tab", "compose"],
    )
    assert "Welcome" in out[0]
    assert out[1].strip() and "inbox" in out[1].lower()
    assert out[2].strip() and "compose" in out[2].lower()


def test_pace_lines_empty_without_hints_stays_empty():
    assert narration.pace_lines(["hi", "", ""])[1:] == ["", ""]


def test_merge_demo_lines_combines_recorded_and_hint():
    def ask(prompt: str) -> str:
        assert "inbox" in prompt.lower()
        return '{"lines": ["Here is the inbox where every conversation lands."]}'

    out = narration.merge_demo_lines(
        ["open inbox"], ["inbox tab"], ask_text=ask
    )
    assert "inbox" in out[0].lower()
    assert len(out) == 1


def test_merge_demo_lines_noop_without_asker():
    lines = ["Open inbox.", ""]
    assert narration.merge_demo_lines(lines, ["a", "b"], ask_text=None) == lines


def test_merge_demo_lines_keeps_paced_when_llm_blanks():
    def ask(_prompt: str) -> str:
        return '{"lines": [""]}'

    out = narration.merge_demo_lines(["Open the inbox now."], ["inbox"], ask_text=ask)
    assert out == ["Open the inbox now."]


def test_paced_speech_windows_match_line_length_not_monologue():
    lines = ["Short intro here.", "Now the next click."]
    times = [500, 5000]
    wins = narration.paced_speech_windows(lines, times)
    assert wins[0] is not None and wins[1] is not None
    assert wins[0][1] - wins[0][0] < 10_000
    assert wins[1][1] - wins[1][0] < 10_000
    assert wins[0][0] <= times[0]
    assert wins[1][0] <= times[1]


def test_compact_timeline_closes_monologue_holes():
    lines = ["Short intro here.", "Now the next click.", "And save."]
    clicks, windows = narration.compact_timeline(lines)
    assert clicks == sorted(clicks)
    assert clicks[-1] < 30_000
    assert all(w is not None for w in windows)
    # Click lands during the line, not 45s later.
    assert windows[0][0] <= clicks[0] <= windows[0][1]


def test_paced_speech_windows_skip_silent():
    wins = narration.paced_speech_windows(["One.", "", "Three."], [0, 2000, 4000])
    assert wins[0] is not None
    assert wins[1] is None
    assert wins[2] is not None


def test_rebuild_flow_narration_paces_and_windows():
    lines, timings, windows, clicks = narration.rebuild_flow_narration(
        lines=[_MONO, "", ""],
        step_times_ms=[500, 4000, 8000],
        hints=["home", "inbox", "compose"],
        ask_text=None,
    )
    assert all(l.strip() for l in lines)
    assert all(len(l.split()) <= narration.MAX_WORDS for l in lines)
    assert timings and timings[0]["idx"] == 0
    assert windows[0] is not None
    assert windows[0][1] - windows[0][0] < 30_000
    assert clicks[-1] < 60_000


def test_narrate_recording_paces_monologue_across_silent_clicks():
    @dataclass
    class _Aliased:
        at_ms: int
        alias: str = ""

    steps = [
        _Aliased(0, "dashboard"),
        _Aliased(2000, "campaigns"),
        _Aliased(5000, "create"),
    ]

    def transcribe_verbose(**_kw):
        return {
            "segments": [
                {"start": 0.0, "end": 20.0, "text": _MONO},
            ]
        }

    lines, timings, windows = narration.narrate_recording(
        audio=b"fake",
        steps=steps,
        api_key="test",
        ask_text=None,
        transcribe_verbose=transcribe_verbose,
        language="en",
    )
    assert len(lines) == 3
    assert all(l.strip() for l in lines)
    assert all(len(l.split()) <= narration.MAX_WORDS for l in lines)
    assert all(w is not None for w in windows)
    assert timings[0]["idx"] == 0
