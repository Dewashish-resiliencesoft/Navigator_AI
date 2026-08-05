"""Split one long crawl into coherent demo flows.

A 20-step explore that touches billing then analytics then settings is three
demos, not one. Segmentation uses semantic step labels (completion verbs +
section changes) so the Client reviews named flows instead of a blob.

Guards the pathological cases:
  - a 1-step segment merges into its neighbour
  - a run with no completion signal stays one flow
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from navigator.automation.explore.semantics import FlowSemantics, label_flow
from navigator.automation.record import RecordedStep

#: Verbs that usually close a unit of work in a SaaS UI.
#: Stem + optional -s/-ed/-ing so "Saves the invoice" still counts.
_COMPLETION = re.compile(
    r"\b("
    r"saves?|saving|saved|"
    r"submits?|submitting|submitted|"
    r"creates?|creating|created|"
    r"confirms?|confirming|confirmed|"
    r"publishes?|publishing|published|"
    r"sends?|sending|sent|"
    r"finishes?|finishing|finished|"
    r"completes?|completing|completed|"
    r"applies?|applying|applied|"
    r"done"
    r")\b",
    re.I,
)

#: Soft section markers in labels / element text (nav into a new area).
_SECTION = re.compile(
    r"\b(billing|invoice|analytics|settings|dashboard|reports?|contacts?|"
    r"messages?|inbox|campaigns?|projects?|team|users?|profile|account)\b",
    re.I,
)


@dataclass(frozen=True)
class Segment:
    """One coherent stretch of the crawl, ready to persist as a flow."""

    steps: tuple[RecordedStep, ...]
    labels: tuple[str, ...]
    semantics: FlowSemantics = field(default_factory=FlowSemantics)

    @property
    def start_idx(self) -> int:
        return 0  # relative; absolute index tracked by caller if needed


def segment_steps(
    steps: Sequence[RecordedStep],
    labels: Sequence[str],
    *,
    ask_text: Callable[[str], str] | None = None,
) -> list[Segment]:
    """Split `steps` into coherent flows, then name each via `label_flow`.

    `labels` is parallel to `steps` ("" when unlabelled). Length mismatch is
    padded with "" rather than raising — a partial labelling run must still
    produce a usable draft.
    """
    if not steps:
        return []

    padded = list(labels[: len(steps)]) + [""] * max(0, len(steps) - len(labels))
    cuts = _cut_indices(steps, padded)
    raw = _slices(steps, padded, cuts)
    merged = _merge_singletons(raw)

    out: list[Segment] = []
    for seg_steps, seg_labels in merged:
        sem = label_flow(list(seg_labels), ask_text=ask_text)
        out.append(Segment(steps=tuple(seg_steps), labels=tuple(seg_labels), semantics=sem))
    return out


def _cut_indices(steps: Sequence[RecordedStep], labels: Sequence[str]) -> list[int]:
    """Indices AFTER which a new segment begins (exclusive end of prior)."""
    cuts: list[int] = []
    prev_section = _section_of(labels[0], steps[0]) if steps else ""
    for i in range(len(steps)):
        label = labels[i]
        # Completion verb on this step → cut after it (when not the last step).
        if i < len(steps) - 1 and _COMPLETION.search(label or ""):
            cuts.append(i + 1)
            prev_section = _section_of(labels[i + 1], steps[i + 1]) if i + 1 < len(steps) else prev_section
            continue
        section = _section_of(label, steps[i])
        if (
            i > 0
            and section
            and prev_section
            and section != prev_section
            and i not in cuts
        ):
            # Section change: cut *before* this step so the new area starts clean.
            if cuts and cuts[-1] == i:
                pass
            else:
                cuts.append(i)
        if section:
            prev_section = section
    return sorted(set(c for c in cuts if 0 < c < len(steps)))


def _section_of(label: str, step: RecordedStep) -> str:
    blob = f"{label} {step.alias or ''} {step.tool or ''}"
    m = _SECTION.search(blob)
    return m.group(1).lower() if m else ""


def _slices(
    steps: Sequence[RecordedStep],
    labels: Sequence[str],
    cuts: Sequence[int],
) -> list[tuple[list[RecordedStep], list[str]]]:
    bounds = [0, *cuts, len(steps)]
    out: list[tuple[list[RecordedStep], list[str]]] = []
    for a, b in zip(bounds, bounds[1:]):
        if a >= b:
            continue
        out.append((list(steps[a:b]), list(labels[a:b])))
    return out or [(list(steps), list(labels))]


def _merge_singletons(
    slices: list[tuple[list[RecordedStep], list[str]]],
) -> list[tuple[list[RecordedStep], list[str]]]:
    """A 1-step segment is almost never a demo — fold it into a neighbour."""
    if len(slices) <= 1:
        return slices
    out: list[tuple[list[RecordedStep], list[str]]] = []
    for steps, labels in slices:
        if len(steps) == 1 and out:
            # Merge into previous.
            prev_s, prev_l = out[-1]
            out[-1] = (prev_s + steps, prev_l + labels)
        elif len(steps) == 1 and not out:
            # Hold; may merge into next when we see it.
            out.append((steps, labels))
        else:
            if out and len(out[-1][0]) == 1:
                # Previous was a held singleton — merge it forward into this.
                held_s, held_l = out.pop()
                out.append((held_s + steps, held_l + labels))
            else:
                out.append((steps, labels))
    return out
