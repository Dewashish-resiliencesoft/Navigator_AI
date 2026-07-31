"""Publish a local port via cloudflared quick tunnel.

Important: after the public URL appears we keep draining cloudflared's stdout in
a background thread. If the pipe fills, cloudflared blocks and the tunnel dies —
Meet then shows Cloudflare Error 1033.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


@dataclass
class TunnelHandle:
    public_url: str
    _proc: subprocess.Popen[str]
    _drain: threading.Thread | None = field(default=None, repr=False)

    def stop(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()


_URL_RE = re.compile(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com")


def _drain_stdout(proc: subprocess.Popen[str]) -> None:
    """Prevent stdout pipe backpressure from killing the tunnel."""
    assert proc.stdout is not None
    try:
        for _line in proc.stdout:
            if proc.poll() is not None:
                break
    except Exception:
        return


def start_tunnel(local_port: int, binary: str = "cloudflared") -> TunnelHandle:
    path = Path(binary)
    if binary != "cloudflared" and not path.is_file():
        raise RuntimeError(f"tunnel binary not found: {binary}")

    proc = subprocess.Popen(
        [binary, "tunnel", "--url", f"http://127.0.0.1:{local_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    public: str | None = None
    deadline = time.time() + 60
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        match = _URL_RE.search(line or "")
        if match:
            public = match.group(0)
            break

    if not public:
        proc.kill()
        raise RuntimeError(
            f"tunnel did not publish a URL (is {binary!r} installed and on PATH?)"
        )

    if proc.poll() is not None:
        raise RuntimeError("cloudflared exited right after publishing URL")

    drain = threading.Thread(target=_drain_stdout, args=(proc,), daemon=True)
    drain.start()

    handle = TunnelHandle(public_url=public, _proc=proc, _drain=drain)
    wait_until_public(f"{public}/view", timeout_s=30)
    return handle


def wait_until_public(url: str, *, timeout_s: float = 30.0) -> None:
    """Fail fast if the edge cannot reach our local relay (avoids Meet 1033)."""
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=5) as resp:
                if 200 <= getattr(resp, "status", 200) < 300:
                    return
                last = f"HTTP {resp.status}"
        except URLError as e:
            last = str(e)
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(1)
    raise RuntimeError(f"public tunnel URL not reachable: {url} ({last})")
