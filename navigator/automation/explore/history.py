"""Read past explore episodes so run N+1 starts smarter than run N.

This is the agent's *own* observed history — not a rewritten correction rule —
so no human gate is needed. Cap hard: last 5 episodes, ≤200 entries. A
long-lived product must not unbounded the reasoner prompt.

Corrupt JSONL lines are skipped, never fatal. A missing directory is empty
history, not an error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from navigator.automation.explore.episode import StepAttempt

#: How many prior runs to consult. Older than this is noise.
MAX_EPISODES = 5
#: Hard cap across known-bad + proven entries feeding the next run.
MAX_ENTRIES = 200

#: Tactics the repair ladder actually knows how to run. Anything else in a
#: history file is ignored — a corrupt or hand-edited file must not invent
#: behaviour.
KNOWN_TACTICS = frozenset(
    {
        "reperceive_refind",
        "alternate_selector",
        "scroll_into_view",
        "dismiss_overlay",
        "retry",
        "wait_settle",
        "relax_verify",
        "vlm_locate",
    }
)


@dataclass(frozen=True)
class LoadedEpisode:
    """One prior run, parsed enough for history consumers."""

    product_id: str
    job_id: str
    attempts: tuple[StepAttempt, ...] = ()
    mtime: float = 0.0


def url_path(url: str) -> str:
    """Path only — query/fragment churn must not split the same page."""
    return urlparse(url or "").path or "/"


def load_recent(
    root: Path | str,
    product_id: str,
    *,
    limit: int = MAX_EPISODES,
) -> list[LoadedEpisode]:
    """Newest-first prior episodes for one product. Empty when nothing on disk."""
    product_dir = Path(root) / product_id
    if not product_dir.is_dir():
        return []

    jobs: list[tuple[float, Path]] = []
    for job_dir in product_dir.iterdir():
        if not job_dir.is_dir():
            continue
        try:
            mtime = job_dir.stat().st_mtime
        except OSError:
            continue
        jobs.append((mtime, job_dir))
    jobs.sort(key=lambda pair: pair[0], reverse=True)

    out: list[LoadedEpisode] = []
    for mtime, job_dir in jobs[: max(0, limit)]:
        attempts = _read_attempts(job_dir / "attempts.jsonl")
        out.append(
            LoadedEpisode(
                product_id=product_id,
                job_id=job_dir.name,
                attempts=tuple(attempts),
                mtime=mtime,
            )
        )
    return out


def known_bad(
    root: Path | str,
    product_id: str,
    *,
    limit: int = MAX_EPISODES,
    max_entries: int = MAX_ENTRIES,
) -> dict[str, tuple[str, int]]:
    """element_key → (kind, unrepaired_failure_count), newest episodes first.

    A key that was eventually repaired in a run does not count for that run.
    Counts accumulate across episodes so "failed twice" means two separate
    unrepaired outcomes, not two attempts inside one ladder.
    """
    tallies: dict[str, tuple[str, int]] = {}
    entries = 0
    for ep in load_recent(root, product_id, limit=limit):
        for key, kind in _unrepaired_in(ep.attempts):
            if entries >= max_entries and key not in tallies:
                return tallies
            prev = tallies.get(key)
            if prev is None:
                tallies[key] = (kind, 1)
                entries += 1
            else:
                tallies[key] = (kind or prev[0], prev[1] + 1)
    return tallies


def proven_tactics(
    root: Path | str,
    product_id: str,
    *,
    limit: int = MAX_EPISODES,
    max_entries: int = MAX_ENTRIES,
) -> dict[tuple[str, str], str]:
    """(url_path, stuck_kind) → tactic that eventually succeeded.

    Newer episodes win when the same key appears twice — the UI may have
    changed, and the most recent repair is the one worth trying first.
    """
    out: dict[tuple[str, str], str] = {}
    for ep in reversed(load_recent(root, product_id, limit=limit)):
        for path, kind, tactic in _successful_repairs_in(ep.attempts):
            if tactic not in KNOWN_TACTICS:
                continue
            key = (path, kind)
            if key not in out and len(out) >= max_entries:
                continue
            out[key] = tactic
    return out


def _read_attempts(path: Path) -> list[StepAttempt]:
    if not path.is_file():
        return []
    out: list[StepAttempt] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        attempt = _attempt_from_dict(data)
        if attempt is not None:
            out.append(attempt)
    return out


def _attempt_from_dict(data: Any) -> StepAttempt | None:
    if not isinstance(data, dict):
        return None
    try:
        return StepAttempt(
            element_key=str(data.get("element_key") or ""),
            alias=str(data.get("alias") or ""),
            selector=str(data.get("selector") or ""),
            tool=str(data.get("tool") or ""),
            attempt=int(data.get("attempt") or 0),
            tactic=str(data.get("tactic") or ""),
            kind=str(data.get("kind") or ""),
            ok=bool(data.get("ok")),
            detail=str(data.get("detail") or ""),
            duration_ms=int(data.get("duration_ms") or 0),
            url_before=str(data.get("url_before") or ""),
            url_after=str(data.get("url_after") or ""),
        )
    except (TypeError, ValueError):
        return None


def _unrepaired_in(attempts: tuple[StepAttempt, ...] | list[StepAttempt]) -> list[tuple[str, str]]:
    by_key: dict[tuple[str, str], list[StepAttempt]] = {}
    for a in attempts:
        by_key.setdefault((a.element_key, a.tool), []).append(a)
    out: list[tuple[str, str]] = []
    for (ek, _tool), seq in by_key.items():
        if not ek:
            continue
        last = seq[-1]
        if last.ok:
            continue
        # Prefer the original failure's kind (attempt 0); fall back to last.
        kind = next((s.kind for s in seq if s.kind), last.kind) or "unknown"
        out.append((ek, kind))
    return out


def _successful_repairs_in(
    attempts: tuple[StepAttempt, ...] | list[StepAttempt],
) -> list[tuple[str, str, str]]:
    by_key: dict[tuple[str, str], list[StepAttempt]] = {}
    for a in attempts:
        by_key.setdefault((a.element_key, a.tool), []).append(a)
    out: list[tuple[str, str, str]] = []
    for seq in by_key.values():
        if not (seq[-1].ok and any(s.attempt > 0 for s in seq)):
            continue
        original = next((s for s in seq if s.attempt == 0), seq[0])
        winner = next((s for s in reversed(seq) if s.ok and s.attempt > 0), None)
        if winner is None or not winner.tactic:
            continue
        kind = original.kind or "unknown"
        out.append((url_path(original.url_before), kind, winner.tactic))
    return out
