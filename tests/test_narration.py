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


def test_refine_returns_input_on_failure():
    lines = ["umm so basically open inbox"]
    assert narration.refine(lines, ask_text=None) == lines


def test_refine_applies_llm_cleanup():
    def ask(_prompt: str) -> str:
        return '{"lines": ["Open the inbox."]}'

    out = narration.refine(["umm open inbox"], ask_text=ask)
    assert out == ["Open the inbox."]


def test_skip_indices_drops_silent_rapid_taps():
    lines = ["hello", "", ""]
    times = [0, 200, 350]
    assert narration.skip_indices(lines, times) == {1, 2}


def test_narrate_recording_end_to_end():
    steps = [_Step(0), _Step(5000), _Step(10000)]

    def transcribe_verbose(**_kw):
        return {
            "segments": [
                {"start": 0.5, "end": 1.5, "text": "First bit"},
                {"start": 5.5, "end": 6.5, "text": "Second bit"},
            ]
        }

    def ask(_prompt: str) -> str:
        return '{"lines": ["First clean.", "Second clean."]}'

    lines, timings = narration.narrate_recording(
        audio=b"fake",
        steps=steps,
        api_key="test",
        ask_text=ask,
        transcribe_verbose=transcribe_verbose,
    )
    assert any("First" in l for l in lines)
    assert any("Second" in l for l in lines)
    assert timings[0]["idx"] == 0
    assert timings[0]["speak_ms"] == 5000
