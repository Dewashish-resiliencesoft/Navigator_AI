"""Keep the screenshare renderer below the Zoom bot's audio on CPU (round 12).

During demos the laptop ran at load ~15 on 8 cores; the webpage-streamer and
its Chrome took ~100% while the bot's Chrome (which plays our audio) needed
110%. Renicing the streamer tree +5 lets the audio path win. Best effort:
runs every 10s (the streamer respawns Chrome per demo), never raises.
"""
from __future__ import annotations

import os
import subprocess
import threading

_STARTED = False
NICE = 5


def _tree(pid: int) -> list[int]:
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        try:
            kids = subprocess.run(["pgrep", "-P", str(p)], capture_output=True, text=True, timeout=2).stdout.split()
        except Exception:  # noqa: BLE001
            kids = []
        todo.extend(int(k) for k in kids if k.isdigit())
    return out


def renice_streamer_once() -> int:
    try:
        roots = subprocess.run(["pgrep", "-f", "run_webpage_streamer"], capture_output=True, text=True,
                               timeout=2).stdout.split()
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for r in roots:
        for p in _tree(int(r)):
            try:
                if os.getpriority(os.PRIO_PROCESS, p) < NICE:
                    os.setpriority(os.PRIO_PROCESS, p, NICE)
                    n += 1
            except Exception:  # noqa: BLE001
                pass
    return n


def start() -> None:
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    stop = threading.Event()

    def loop() -> None:
        first = True
        while not stop.wait(0.1 if first else 10.0):
            n = renice_streamer_once()
            if n:
                print(f"[cpu] reniced {n} webpage-streamer process(es) to +{NICE}", flush=True)
            first = False

    threading.Thread(target=loop, name="streamer-renice", daemon=True).start()
