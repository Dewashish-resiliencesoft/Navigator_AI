"""Latency instrumentation for the realtime voice-agent path.

Records wall-clock durations between the key gates defined in the spec.
All measurements are stored in-process and can be flushed to the action
log or emitted as structured events.

Gates measured:
  user_speech_end → gemini_response_start       (perceived voice latency)
  user_request    → acknowledgement_start        (task ack latency)
  task_handoff    → flash_start                  (delegation overhead)
  flash_start     → first_browser_action         (planning latency)
  browser_action_start → browser_action_complete
  browser_action_complete → verification_complete
  result_ready    → gemini_response_start        (result delivery latency)
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LatencySpan:
    name: str
    start: float
    end: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_ms(self) -> float | None:
        if self.end is None:
            return None
        return (self.end - self.start) * 1000

    def finish(self, **meta: Any) -> "LatencySpan":
        self.end = time.monotonic()
        self.metadata.update(meta)
        return self


class LatencyTracker:
    """Thread-safe latency recorder for one demo session."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._lock = threading.Lock()
        self._spans: list[LatencySpan] = []
        self._open: dict[str, LatencySpan] = {}

    def start(self, gate: str, **meta: Any) -> LatencySpan:
        span = LatencySpan(name=gate, start=time.monotonic(), metadata=dict(meta))
        with self._lock:
            self._open[gate] = span
            self._spans.append(span)
        return span

    def finish(self, gate: str, **meta: Any) -> LatencySpan | None:
        with self._lock:
            span = self._open.pop(gate, None)
        if span is None:
            return None
        span.finish(**meta)
        return span

    def record(self, gate: str, duration_ms: float, **meta: Any) -> LatencySpan:
        """Record a pre-measured duration without open/finish."""
        now = time.monotonic()
        span = LatencySpan(
            name=gate,
            start=now - duration_ms / 1000,
            end=now,
            metadata=dict(meta),
        )
        with self._lock:
            self._spans.append(span)
        return span

    def summary(self) -> list[dict]:
        with self._lock:
            spans = list(self._spans)
        result = []
        for s in spans:
            result.append({
                "gate": s.name,
                "elapsed_ms": s.elapsed_ms,
                "metadata": s.metadata,
            })
        return result

    def log_summary(self, *, prefix: str = "[latency]") -> None:
        for row in self.summary():
            ms = row["elapsed_ms"]
            label = f"{ms:.0f}ms" if ms is not None else "open"
            print(f"{prefix} {row['gate']}: {label}", flush=True)


# Convenience gate name constants
USER_SPEECH_END = "user_speech_end→gemini_response_start"
ACK_LATENCY = "user_request→acknowledgement_start"
DELEGATION_OVERHEAD = "task_handoff→flash_start"
PLANNING_LATENCY = "flash_start→first_browser_action"
BROWSER_ACTION = "browser_action_start→browser_action_complete"
VERIFICATION = "browser_action_complete→verification_complete"
RESULT_DELIVERY = "result_ready→gemini_response_start"
