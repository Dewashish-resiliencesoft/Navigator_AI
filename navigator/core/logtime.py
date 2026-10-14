"""Timestamp demo log lines: 'HH:MM:SS.mmm +Δms [tag] ...' (box-local time).

Installed once by wrapping sys.stdout; only lines starting with a demo tag are
prefixed so uvicorn access logs stay untouched. Also collects latency samples
for the per-demo summary.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from datetime import datetime

_TAGS = re.compile(r"^\[(live|speak|utterance|audio|voice-in|voice-out|flow|exec|latency|ui-tool|screen-thread|verify|plan|listen)\]")


class _Stamped:
    def __init__(self, inner) -> None:
        self._inner = inner
        self._buf = ""
        self._last = None
        self._lock = threading.Lock()

    def write(self, s: str) -> int:
        with self._lock:
            self._buf += s
            out = []
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if _TAGS.match(line):
                    now = time.monotonic()
                    d = 0 if self._last is None else int((now - self._last) * 1000)
                    self._last = now
                    line = f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} +{d}ms {line}"
                out.append(line + "\n")
            if out:
                self._inner.write("".join(out))
        return len(s)

    def flush(self) -> None:
        self._inner.flush()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def install() -> None:
    if not isinstance(sys.stdout, _Stamped):
        sys.stdout = _Stamped(sys.stdout)


# ---- latency metrics ---------------------------------------------------

_samples: dict[str, list[float]] = {}
_slock = threading.Lock()


def record(metric: str, ms: float, note: str = "") -> None:
    with _slock:
        _samples.setdefault(metric, []).append(float(ms))
    print(f"[latency] {metric}={ms:.0f}ms{(' ' + note) if note else ''}", flush=True)


def summary(reset: bool = True) -> str:
    with _slock:
        items = {k: sorted(v) for k, v in _samples.items() if v}
        if reset:
            _samples.clear()
    parts = []
    for k, v in sorted(items.items()):
        p = lambda q: v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))]  # noqa: E731
        parts.append(f"{k}: n={len(v)} p50={p(0.5):.0f} p95={p(0.95):.0f}")
    line = "[latency] SUMMARY " + (" | ".join(parts) if parts else "no samples")
    print(line, flush=True)
    return line
