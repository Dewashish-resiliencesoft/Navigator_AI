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
    assert timings[0]["speak_ms"] == 5000
    # Windows are the raw speech times, not the refined wording.
    assert windows == [(500, 1500), (5500, 6500), None]


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
