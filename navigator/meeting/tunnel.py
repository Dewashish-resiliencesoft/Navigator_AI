"""Publish a local port via cloudflared quick tunnel."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TunnelHandle:
    public_url: str
    _proc: subprocess.Popen[str]

    def stop(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()


_URL_RE = re.compile(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com")


def start_tunnel(local_port: int, binary: str = "cloudflared") -> TunnelHandle:
    path = Path(binary)
    if not path.is_file() and binary == "cloudflared":
        # fall through to PATH lookup via Popen
        pass
    elif binary != "cloudflared" and not path.is_file():
        raise RuntimeError(f"tunnel binary not found: {binary}")

    proc = subprocess.Popen(
        [binary, "tunnel", "--url", f"http://127.0.0.1:{local_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    public: str | None = None
    deadline = time.time() + 45
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
    return TunnelHandle(public_url=public, _proc=proc)
