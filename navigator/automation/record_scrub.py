"""Drop junk / duplicate steps before a recorded flow is saved."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from navigator.automation.narration import skip_indices

if TYPE_CHECKING:
    from navigator.automation.record import RecordedStep

_THEME_JUNK = re.compile(
    r"(dark[_-]?mode|light[_-]?mode|theme[_-]?toggle|color[_-]?scheme|toggle[_-]?theme)",
    re.I,
)


def is_junk_recorded_step(step: "RecordedStep") -> bool:
    """Theme toggles and other recorder noise that should not ship in a demo flow."""
    alias = (step.alias or "").strip()
    sel = (step.selector or "").strip()
    combined = f"{alias} {sel}".lower()
    if _THEME_JUNK.search(combined):
        return True
    from navigator.automation.record import junk_record_reason

    return (
        junk_record_reason(
            {"tag": "", "text": ""},
            alias=alias,
            selector=sel,
        )
        is not None
    )


def scrub_recorded_steps(steps: list["RecordedStep"]) -> list["RecordedStep"]:
    """Remove junk clicks and silent rapid duplicate taps."""
    kept: list[RecordedStep] = []
    for step in steps:
        if is_junk_recorded_step(step):
            continue
        kept.append(step)
    if not kept:
        return kept
    times = [int(getattr(s, "at_ms", 0) or 0) for s in kept]
    if not any(times):
        return kept
    drop = skip_indices([""] * len(kept), times)
    return [s for i, s in enumerate(kept) if i not in drop]


def step_clicks_payload(steps: list["RecordedStep"]) -> list[dict[str, int]]:
    return [
        {"idx": i, "at_ms": int(getattr(s, "at_ms", 0) or 0)}
        for i, s in enumerate(steps)
    ]


def reindex_meta_list(
    rows: list[dict], *, drop_indices: set[int], shift: bool = True
) -> list[dict]:
    """Re-index meta rows after steps were removed from a flow."""
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row["idx"])
        except (KeyError, TypeError, ValueError):
            continue
        if idx in drop_indices:
            continue
        if shift:
            removed_before = sum(1 for d in drop_indices if d < idx)
            row = {**row, "idx": idx - removed_before}
        out.append(row)
    return out


def step_mouse_paths_payload(steps: list["RecordedStep"]) -> list[dict]:
    out: list[dict] = []
    for i, step in enumerate(steps):
        path = getattr(step, "mouse_path", None) or []
        if path:
            out.append({"idx": i, "points": list(path)})
    return out


def reindex_narration_lines(lines: list, drop_indices: set[int]) -> list:
    return [line for i, line in enumerate(lines) if i not in drop_indices]
