"""Durable explore episodes: JSONL attempts + JSON summary + capped screenshots."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RETENTION_DAYS = 7
MAX_SHOTS_PER_RUN = 20


@dataclass(frozen=True)
class StopReason:
    """Structured end-of-run cause. ``render()`` keeps dashboard copy stable."""

    kind: str
    detail: str = ""

    def render(self) -> str:
        k, d = self.kind, self.detail
        if k == "stopped_by_client":
            return "stopped by client"
        if k == "max_pages":
            return f"max_pages ({d}) reached"
        if k == "max_steps":
            return f"max_steps ({d}) reached"
        if k == "time_budget":
            return f"time budget ({d}s) reached"
        if k == "no_new_elements":
            return "no new interactive elements found"
        if k == "dead_end":
            return f"dead end at {d}"
        if k == "failed":
            return d or "failed"
        return d or k

    @classmethod
    def from_budget_text(cls, text: str) -> StopReason:
        """Parse today's budget_exhausted / explorer stop strings."""
        t = (text or "").strip()
        if t == "stopped by client":
            return cls("stopped_by_client")
        if t.startswith("max_pages"):
            # max_pages (25) reached
            inner = t[t.find("(") + 1 : t.find(")")] if "(" in t else ""
            return cls("max_pages", inner)
        if t.startswith("max_steps"):
            inner = t[t.find("(") + 1 : t.find(")")] if "(" in t else ""
            return cls("max_steps", inner)
        if t.startswith("time budget"):
            inner = t[t.find("(") + 1 : t.find("s)")] if "(" in t else ""
            return cls("time_budget", inner)
        if t == "no new interactive elements found":
            return cls("no_new_elements")
        if t.startswith("dead end at "):
            return cls("dead_end", t[len("dead end at ") :])
        return cls("other", t)


@dataclass(frozen=True)
class StepAttempt:
    element_key: str
    alias: str
    selector: str
    tool: str
    attempt: int  # 0 = original, 1..n = repairs
    tactic: str  # "" for the original attempt
    kind: str  # StuckKind or "" when ok
    ok: bool
    detail: str
    duration_ms: int
    url_before: str
    url_after: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EpisodeStore:
    """One explore run on disk under ``{root}/{product_id}/{job_id}/``."""

    root: Path
    product_id: str
    job_id: str
    max_shots: int = MAX_SHOTS_PER_RUN
    retention_days: int = RETENTION_DAYS
    attempts: list[StepAttempt] = field(default_factory=list)
    repairs_used: int = 0
    _shots_written: int = 0
    _opened_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self._purge_old()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.shots_dir.mkdir(parents=True, exist_ok=True)

    @property
    def dir(self) -> Path:
        return self.root / self.product_id / self.job_id

    @property
    def shots_dir(self) -> Path:
        return self.dir / "shots"

    @property
    def attempts_path(self) -> Path:
        return self.dir / "attempts.jsonl"

    @property
    def episode_path(self) -> Path:
        return self.dir / "episode.json"

    def record(self, attempt: StepAttempt) -> None:
        self.attempts.append(attempt)
        if attempt.attempt > 0:
            self.repairs_used += 1
        with self.attempts_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(attempt.as_dict(), ensure_ascii=False) + "\n")

    def save_shot(self, jpeg_bytes: bytes) -> str | None:
        """Write an unrepaired-failure screenshot. None when cap hit or empty."""
        if not jpeg_bytes or self._shots_written >= self.max_shots:
            return None
        name = f"{self._shots_written:03d}.jpg"
        path = self.shots_dir / name
        path.write_bytes(jpeg_bytes)
        self._shots_written += 1
        return name

    def finalize(
        self,
        *,
        stop_reason: StopReason | None,
        budget: dict[str, Any],
        steps: int,
        actions_taken: int,
    ) -> dict[str, Any]:
        kind_tallies: dict[str, int] = {}
        repair_ok = 0
        unrepaired = 0
        # Group by (element_key, tool) sequences: last attempt wins.
        by_key: dict[tuple[str, str], list[StepAttempt]] = {}
        for a in self.attempts:
            by_key.setdefault((a.element_key, a.tool), []).append(a)
        for seq in by_key.values():
            last = seq[-1]
            if last.ok and any(s.attempt > 0 for s in seq):
                repair_ok += 1
            if not last.ok:
                unrepaired += 1
                if last.kind:
                    kind_tallies[last.kind] = kind_tallies.get(last.kind, 0) + 1

        summary = {
            "product_id": self.product_id,
            "job_id": self.job_id,
            "stop_reason": (
                {"kind": stop_reason.kind, "detail": stop_reason.detail}
                if stop_reason
                else None
            ),
            "stop_reason_text": stop_reason.render() if stop_reason else "",
            "budget": budget,
            "steps": steps,
            "actions_taken": actions_taken,
            "attempts": len(self.attempts),
            "repairs_used": self.repairs_used,
            "repair_successes": repair_ok,
            "unrepaired_failures": unrepaired,
            "kind_tallies": kind_tallies,
            "shots": self._shots_written,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "wall_clock_s": round(time.time() - self._opened_at, 1),
        }
        self.episode_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return summary

    def successful_repairs(self) -> list[list[StepAttempt]]:
        """Sequences where a repair attempt eventually succeeded."""
        by_key: dict[tuple[str, str], list[StepAttempt]] = {}
        for a in self.attempts:
            by_key.setdefault((a.element_key, a.tool), []).append(a)
        out: list[list[StepAttempt]] = []
        for seq in by_key.values():
            if seq[-1].ok and any(s.attempt > 0 for s in seq):
                out.append(seq)
        return out

    def unrepaired_failures(self) -> list[list[StepAttempt]]:
        by_key: dict[tuple[str, str], list[StepAttempt]] = {}
        for a in self.attempts:
            by_key.setdefault((a.element_key, a.tool), []).append(a)
        return [seq for seq in by_key.values() if not seq[-1].ok]

    def _purge_old(self) -> None:
        """Delete episode dirs older than retention_days (any product under root)."""
        root = self.root
        if not root.is_dir():
            return
        cutoff = time.time() - self.retention_days * 86400
        for product_dir in root.iterdir():
            if not product_dir.is_dir():
                continue
            for job_dir in product_dir.iterdir():
                if not job_dir.is_dir():
                    continue
                try:
                    mtime = job_dir.stat().st_mtime
                except OSError:
                    continue
                if mtime < cutoff:
                    shutil.rmtree(job_dir, ignore_errors=True)
