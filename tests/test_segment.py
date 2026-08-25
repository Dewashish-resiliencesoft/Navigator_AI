"""Segment one explore crawl into coherent flows."""

from __future__ import annotations

from navigator.automation.explore.segment import segment_steps
from navigator.automation.record import RecordedStep


def _step(alias: str = "x") -> RecordedStep:
    return RecordedStep(tool="click_element", alias=alias, selector=f"#{alias}")


def test_no_completion_signal_stays_one_flow():
    steps = [_step(f"n{i}") for i in range(8)]
    labels = [f"Opens panel {i}" for i in range(8)]
    segs = segment_steps(steps, labels, ask_text=None)
    assert len(segs) == 1
    assert len(segs[0].steps) == 8


def test_completion_signals_split_flows():
    """A 20-step run with 3 completion verbs yields a handful of flows, not 20."""
    steps = [_step(f"s{i}") for i in range(20)]
    labels = [f"Clicks thing {i}" for i in range(20)]
    # Completions at indices 4, 11, 17 → cuts after those → 4 segments before merge.
    for i, verb in ((4, "Saves the invoice"), (11, "Sends the message"), (17, "Publishes the post")):
        labels[i] = verb
    segs = segment_steps(steps, labels, ask_text=None)
    assert 3 <= len(segs) <= 4, f"got {len(segs)} segments"
    assert sum(len(s.steps) for s in segs) == 20


def test_one_step_segment_merges_into_neighbour():
    steps = [_step("a"), _step("b"), _step("c"), _step("d"), _step("e")]
    labels = [
        "Opens billing",
        "Saves the draft",  # cut after → left with [c] alone if next also cuts early
        "Opens analytics",
        "Views chart",
        "Sends report",
    ]
    segs = segment_steps(steps, labels, ask_text=None)
    assert all(len(s.steps) >= 2 for s in segs), f"singleton survived: {segs}"
    assert sum(len(s.steps) for s in segs) == 5


def test_empty_steps_yields_nothing():
    assert segment_steps([], [], ask_text=None) == []


def test_label_flow_called_per_segment():
    calls: list[list[str]] = []

    def ask(prompt: str) -> str:
        # Capture the step listing lines from the prompt.
        lines = [ln for ln in prompt.splitlines() if ln[:1].isdigit() or ln[:2].lstrip().isdigit()]
        calls.append(lines)
        return '{"name": "X", "purpose": "Does X", "tags": ["x"]}'

    steps = [_step(f"s{i}") for i in range(6)]
    labels = [
        "Opens form",
        "Fills name",
        "Saves the record",
        "Opens list",
        "Filters rows",
        "Sends export",
    ]
    segs = segment_steps(steps, labels, ask_text=ask)
    assert len(segs) >= 2
    assert len(calls) == len(segs)
    assert all(s.semantics.purpose == "Does X" for s in segs)
