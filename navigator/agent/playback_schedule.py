"""Turn a recorded flow into a cue schedule on one absolute clock.

A recording is a timeline: the host started talking at 00:03, clicked at 00:05,
went quiet, clicked again at 00:21. Replaying it as "wait a bit, then do the next
thing" loses both the pacing and the relationship between speech and click --
error accumulates, and narration ends up landing on the wrong action.

So instead: lay every event out on one clock up front, then have playback wait
until each event's time. Two kinds of event, per step:

    SPEAK  start narrating step N
    ACT    execute step N's browser action

`ACT` comes from ``_meta.step_clicks``. `SPEAK` comes from ``_meta.step_speech``
-- when the host's voice actually started, per step. The gap between them is the
lead-in the host performed, and reproducing it per-step is the whole point.

Pure transforms over recorded data: no I/O, no browser, no CallDeps. Every
timing rule lives here so it can be tested without a demo running.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

#: Spacing assumed when a flow has neither click times nor narration timing.
DEFAULT_STEP_MS = 3000
#: Conversational TTS pace used when the WAV is not cached yet (~150 wpm).
_ESTIMATE_MS_PER_WORD = 400
_ESTIMATE_FLOOR_MS = 600

CueKind = Literal["speak", "act"]


@dataclass(frozen=True)
class Cue:
    """One scheduled event. ``at_ms`` is on the flow's clock, zero at flow start."""

    idx: int
    kind: CueKind
    at_ms: int
    text: str = ""
    """Narration to speak. Empty for ``act`` cues."""


def fmt_ms(ms: int) -> str:
    """``mm:ss`` -- same format the narrate widget's counter shows while recording."""
    total_s = max(0, int(ms)) // 1000
    return f"{total_s // 60:02d}:{total_s % 60:02d}"


def estimate_spoken_ms(text: str) -> int:
    """Rough spoken length when exact WAV duration is not available yet."""
    words = len((text or "").split())
    if words <= 0:
        return 0
    return max(_ESTIMATE_FLOOR_MS, words * _ESTIMATE_MS_PER_WORD)


def _act_times(
    n_steps: int, clicks: dict[int, int], timing: dict[int, int]
) -> list[int]:
    """Absolute ms per step for the browser action."""
    if clicks:
        out: list[int] = []
        prev = 0
        for i in range(n_steps):
            at = int(clicks.get(i, prev + DEFAULT_STEP_MS) or 0)
            out.append(at)
            prev = at
        return out
    if timing:
        # No click times (older recording): pace off narration length instead.
        # Step 0 fires immediately -- its speak_ms is how long it *lasts*, not
        # how long to wait before it.
        out = []
        cumulative = 0
        for i in range(n_steps):
            out.append(cumulative)
            cumulative += max(0, int(timing.get(i, 0) or 0))
        return out
    return [i * DEFAULT_STEP_MS for i in range(n_steps)]


def _line(lines: Sequence[str], i: int) -> str:
    return str(lines[i]) if i < len(lines) and lines[i] is not None else ""


def build_schedule(
    *,
    n_steps: int,
    clicks: dict[int, int],
    speech: dict[int, tuple[int, int]],
    lines: Sequence[str],
    timing: dict[int, int],
    tts_ms: Callable[[str], int | None],
) -> tuple[list[Cue], int]:
    """Cues sorted by time, plus total flow length in ms.

    ``tts_ms`` gives the exact duration of a synthesized line, or None when it
    has not been synthesized yet — then ``max(recorded window, word estimate)``
    is used so a long line still stretches later cues instead of letting clicks
    race ahead of still-playing narration.
    """
    if n_steps <= 0:
        return [], 0

    act = _act_times(n_steps, clicks, timing)
    speak = [
        speech[i][0] if i in speech else act[i]
        for i in range(n_steps)
    ]
    narrated = [bool(_line(lines, i).strip()) for i in range(n_steps)]

    def duration(i: int) -> int:
        line = _line(lines, i)
        spoken = tts_ms(line)
        if spoken is not None and spoken > 0:
            return int(spoken)
        window = speech.get(i)
        if window is not None:
            recorded = max(0, window[1] - window[0])
        else:
            recorded = max(0, int(timing.get(i, 0) or 0))
        # Prefetch miss / cold cache: never schedule a long line as if it were
        # the short host window — later acts would click while the line still
        # describes another screen.
        return max(recorded, estimate_spoken_ms(line))

    def next_narrated(i: int) -> int | None:
        return next((j for j in range(i + 1, n_steps) if narrated[j]), None)

    cues: list[Cue] = []
    shift = 0
    prev_speak = 0
    prev_act = 0
    total = 0

    for i in range(n_steps):
        # Both move by the same shift, so the recorded lead-in between them
        # survives every stretch. This is the sync invariant.
        at_speak = max(speak[i] + shift, prev_speak)
        at_act = max(act[i] + shift, prev_act)

        if narrated[i]:
            spoken_ms = duration(i)
            cues.append(Cue(i, "speak", at_speak, _line(lines, i)))
            prev_speak = at_speak
            total = max(total, at_speak + spoken_ms)

            # Overrunning into the next line? Push everything after this step by
            # the overflow rather than letting audio stack or clip.
            following = next_narrated(i)
            if following is not None:
                overflow = (at_speak + spoken_ms) - (speak[following] + shift)
                if overflow > 0:
                    shift += overflow

        cues.append(Cue(i, "act", at_act))
        prev_act = at_act
        total = max(total, at_act)

    # Stable, so a step's speak cue stays ahead of its act cue on a tie.
    cues.sort(key=lambda cue: cue.at_ms)
    return cues, total
