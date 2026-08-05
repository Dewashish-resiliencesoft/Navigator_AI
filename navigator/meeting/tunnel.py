"""Publish a local port via cloudflared quick tunnel.

Important: after the public URL appears we keep draining cloudflared's stdout in
a background thread. If the pipe fills, cloudflared blocks and the tunnel dies —
Meet then shows Cloudflare Error 1033.
"""

from __future__ import annotations

import http.client
import re
import socket
import ssl
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
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


def resolve_tunnel_bin(binary: str = "cloudflared") -> str:
    """Return an executable path for cloudflared (or the configured tunnel binary)."""
    from shutil import which

    raw = (binary or "cloudflared").strip() or "cloudflared"
    if raw != "cloudflared":
        path = Path(raw)
        if path.is_file():
            return str(path)
        found = which(raw)
        return found or raw
    found = which("cloudflared")
    if found:
        return found
    default = Path("/usr/local/bin/cloudflared")
    if default.is_file():
        return str(default)
    return "cloudflared"


def tunnel_binary_available(binary: str = "cloudflared") -> bool:
    resolved = resolve_tunnel_bin(binary)
    from shutil import which

    return Path(resolved).is_file() or which(resolved) is not None


def _drain_stdout(proc: subprocess.Popen[str]) -> None:
    """Prevent stdout pipe backpressure from killing the tunnel."""
    assert proc.stdout is not None
    try:
        for _line in proc.stdout:
            if proc.poll() is not None:
                break
    except Exception:
        return


def start_tunnel(
    local_port: int,
    binary: str = "cloudflared",
    *,
    ready_path: str | None = "/view",
) -> TunnelHandle:
    binary = resolve_tunnel_bin(binary)
    if not tunnel_binary_available(binary):
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
    # Relay serves /view; raw WS audio hubs have no HTTP page — skip probe.
    if ready_path:
        wait_until_public(f"{public}{ready_path}", timeout_s=45)
    return handle


def _attendee_webpage_streamer_container() -> str | None:
    try:
        out = subprocess.check_output(
            [
                "docker",
                "ps",
                "--filter",
                "name=webpage-streamer",
                "--format",
                "{{.Names}}",
            ],
            text=True,
            timeout=5,
        ).strip()
        if not out:
            return None
        return out.splitlines()[0]
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def verify_attendee_docker_dns(hostname: str) -> None:
    """Fail fast when Attendee's webpage-streamer Chromium cannot resolve the host.

    ``wait_until_public`` may pass via dig@1.1.1.1 while Docker's stub resolver
    still NXDOMAINs fresh ``*.trycloudflare.com`` names.
    """
    container = _attendee_webpage_streamer_container()
    if not container:
        return
    script = f"import socket\nsocket.getaddrinfo({hostname!r}, 443)\n"
    try:
        subprocess.run(
            ["docker", "exec", container, "python", "-c", script],
            check=True,
            timeout=15,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Attendee container {container!r} cannot resolve {hostname!r} — "
            "Meet screenshare will show DNS_PROBE_FINISHED_NXDOMAIN. "
            "In ~/projects/attendee/local.docker-compose.yaml set "
            "dns: [127.0.0.11, 1.1.1.1, 8.8.8.8] on attendee-webpage-streamer-local "
            "(127.0.0.11 must stay first for compose service names), then "
            "recreate: docker compose -f dev.docker-compose.yaml "
            "-f local.docker-compose.yaml --profile webpage-streamer up -d "
            "--force-recreate attendee-webpage-streamer-local"
        ) from exc


def _socket_resolve(host: str) -> list[str]:
    """Fallback: resolve via Python socket when dig is unavailable."""
    import socket as _socket
    try:
        results = _socket.getaddrinfo(host, 443, _socket.AF_INET)
        return list({r[4][0] for r in results})
    except _socket.gaierror:
        return []


def _dig_ips(host: str) -> list[str]:
    """Resolve host via 1.1.1.1 when systemd-resolved fails on trycloudflare CNAMEs."""
    try:
        out = subprocess.check_output(
            ["dig", "+short", host, "A", "@1.1.1.1"],
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        # dig not installed — try Python socket.
        print("[tunnel] dig not found; using socket fallback", flush=True)
        return _socket_resolve(host)
    except subprocess.SubprocessError:
        return []
    ips: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", line):
            ips.append(line)
    return ips


def _probe_via_public_dns(url: str, *, timeout: float = 5.0) -> int:
    """HTTPS GET using dig@1.1.1.1 + SNI (bypasses broken local stub resolver)."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    ips = _dig_ips(host)
    if not ips:
        raise URLError(f"dig@1.1.1.1 returned no A records for {host}")
    ctx = ssl.create_default_context()
    last_err: Exception | None = None
    for ip in ips:
        try:
            sock = socket.create_connection((ip, 443), timeout=timeout)
            ssock = ctx.wrap_socket(sock, server_hostname=host)
            conn = http.client.HTTPSConnection(host, 443, timeout=timeout, context=ctx)
            conn.sock = ssock
            conn.request("GET", path, headers={"Host": host})
            resp = conn.getresponse()
            status = resp.status
            resp.read()
            conn.close()
            return status
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    raise URLError(f"probe via 1.1.1.1 failed for {host}: {last_err}")


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
            # Local stub DNS often cannot resolve fresh *.trycloudflare.com names.
            if "Name or service not known" in last or "nodename" in last.lower():
                try:
                    status = _probe_via_public_dns(url)
                    if 200 <= status < 300:
                        return
                    last = f"HTTP {status} (via 1.1.1.1)"
                except Exception as dig_err:  # noqa: BLE001
                    last = f"{last}; dig-fallback: {dig_err}"
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(1)
    raise RuntimeError(f"public tunnel URL not reachable: {url} ({last})")
