"""Ensure self-hosted Attendee is running before live demos.

When ``NAVIGATOR_ATTENDEE_BASE_URL`` points at localhost, Navigator can
``docker compose up -d`` the Attendee stack on startup so dev and VPS behave
the same: meeting bot always reachable, no manual second terminal.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from navigator.core.settings import settings

_COMPOSE_FILES = ("dev.docker-compose.yaml", "local.docker-compose.yaml")
_COMPOSE_PROFILE = "webpage-streamer"
_COMPOSE_ID = "voice-agents-v4-no-debug-rec"
_STREAMER_SERVICE = "attendee-webpage-streamer-local"


def is_local_attendee_url(base_url: str) -> bool:
    return any(
        h in base_url
        for h in ("localhost", "127.0.0.1", "host.docker.internal")
    )


def attendee_reachable(base_url: str, *, timeout_s: float = 3.0) -> bool:
    """True when Attendee answers at ``base_url`` (401/404 still count as up)."""
    try:
        urlopen(base_url, timeout=timeout_s)
    except HTTPError:
        return True
    except (URLError, OSError):
        return False
    return True


def _in_pytest() -> bool:
    return "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST") is not None


def _compose_dir() -> Path:
    raw = os.environ.get("NAVIGATOR_ATTENDEE_COMPOSE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return settings.attendee_compose_dir.expanduser()


def _sync_attendee_override(compose_dir: Path) -> None:
    """Copy bundled compose override (ENABLE_VOICE_AGENTS) into Attendee clone."""
    import shutil

    src = Path(__file__).resolve().parents[2] / "docker" / "attendee-local.docker-compose.yaml"
    if not src.is_file():
        return
    dst = compose_dir / "local.docker-compose.yaml"
    try:
        shutil.copy2(src, dst)
        print(f"[attendee] synced voice-agent compose → {dst}", flush=True)
    except OSError as exc:
        print(f"[attendee] WARN: compose sync skipped: {exc}", flush=True)
        return
    import importlib.util

    patch_path = Path(__file__).resolve().parents[2] / "scripts" / "disable-attendee-debug-recording.py"
    spec = importlib.util.spec_from_file_location("nav_disable_attendee_debug_rec", patch_path)
    if spec is None or spec.loader is None:
        print("[attendee] WARN: debug-rec patch script missing", flush=True)
        return
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        print(f"[attendee] debug-rec: {mod.patch(compose_dir)}", flush=True)
    except OSError as exc:
        print(f"[attendee] WARN: debug-rec patch skipped: {exc}", flush=True)
    try:
        from navigator.meeting.attendee_ws_patch import patch as patch_audio_ws

        result = patch_audio_ws(compose_dir)
        print(f"[attendee] audio-ws: {result}", flush=True)
        if result.startswith("patched "):
            _restart_attendee_worker(compose_dir)
    except OSError as exc:
        print(f"[attendee] WARN: audio-ws patch skipped: {exc}", flush=True)


def _needs_voice_agent_recreate(compose_dir: Path) -> bool:
    marker = compose_dir / ".navigator-compose-id"
    try:
        current = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    except OSError:
        current = ""
    return current != _COMPOSE_ID


def _mark_voice_agent_compose(compose_dir: Path) -> None:
    try:
        (compose_dir / ".navigator-compose-id").write_text(_COMPOSE_ID, encoding="utf-8")
    except OSError:
        pass


def _docker_compose_up(
    compose_dir: Path, *, force_recreate: bool = False
) -> subprocess.CompletedProcess[str]:
    missing = [f for f in _COMPOSE_FILES if not (compose_dir / f).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Attendee compose files missing in {compose_dir}: {', '.join(missing)}"
        )

    cmd = [
        "docker",
        "compose",
        "-f",
        _COMPOSE_FILES[0],
        "-f",
        _COMPOSE_FILES[1],
        "--profile",
        _COMPOSE_PROFILE,
        "up",
        "-d",
    ]
    if force_recreate:
        cmd.append("--force-recreate")

    proc = subprocess.run(
        cmd,
        cwd=compose_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0 and force_recreate:
        _mark_voice_agent_compose(compose_dir)
    return proc


def _restart_attendee_worker(compose_dir: Path) -> None:
    """Celery workers load Python at boot; a host-volume patch needs restart."""
    proc = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            _COMPOSE_FILES[0],
            "-f",
            _COMPOSE_FILES[1],
            "restart",
            "attendee-worker-local",
        ],
        cwd=compose_dir,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        print(
            f"[attendee] WARN: worker restart failed: {(proc.stderr or proc.stdout or '')[:300]}",
            flush=True,
        )
        return
    print("[attendee] restarted attendee-worker-local (eager audio WS)", flush=True)


def attendee_ui_origin(base_url: str | None = None) -> str:
    """Attendee dashboard origin (strip ``/api/v1`` from API base URL)."""
    base = (base_url or settings.attendee_base_url).rstrip("/")
    if "/api/" in base:
        return base.split("/api/", 1)[0]
    return base


def meeting_sdk_credentials_for_attendee(
    *,
    sdk_client_id: str,
    sdk_client_secret: str,
    s2s_client_id: str,
) -> tuple[str, str] | None:
    """Meeting SDK keys for Attendee. S2S OAuth keys 3712 the Zoom web SDK."""
    sdk_id = (sdk_client_id or "").strip()
    sdk_secret = (sdk_client_secret or "").strip()
    s2s_id = (s2s_client_id or "").strip()
    if not sdk_id or not sdk_secret:
        return None
    if s2s_id and sdk_id == s2s_id:
        return None
    return sdk_id, sdk_secret


def ensure_attendee_zoom_credentials(
    *,
    compose_dir: Path | None = None,
    project_name: str | None = None,
) -> bool:
    """Copy Meeting SDK keys into local Attendee. Never the S2S create/ZAK app."""
    if not is_local_attendee_url(settings.attendee_base_url):
        return True
    creds = meeting_sdk_credentials_for_attendee(
        sdk_client_id=settings.zoom_sdk_client_id,
        sdk_client_secret=settings.zoom_sdk_client_secret,
        s2s_client_id=settings.zoom_client_id,
    )
    if not creds:
        print(
            "[attendee] WARN: NAVIGATOR_ZOOM_SDK_CLIENT_ID/SECRET unset or "
            "same as S2S NAVIGATOR_ZOOM_CLIENT_ID — Attendee cannot join Zoom "
            "until a General App (Meeting SDK on) is saved",
            flush=True,
        )
        return False
    client_id, client_secret = creds

    compose_dir = compose_dir or _compose_dir()
    if not compose_dir.is_dir() or _in_pytest():
        return False

    script = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_attendee_zoom.py"
    if not script.is_file():
        print(f"[attendee] WARN: missing {script}", flush=True)
        return False

    env = os.environ.copy()
    env["NAVIGATOR_ZOOM_CLIENT_ID"] = client_id
    env["NAVIGATOR_ZOOM_CLIENT_SECRET"] = client_secret
    env["NAVIGATOR_ATTENDEE_PROJECT_NAME"] = (
        project_name or os.environ.get("NAVIGATOR_ATTENDEE_PROJECT_NAME") or "Navigator"
    ).strip()

    try:
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                _COMPOSE_FILES[0],
                "-f",
                _COMPOSE_FILES[1],
                "exec",
                "-T",
                "attendee-app-local",
                "env",
                f"NAVIGATOR_ZOOM_CLIENT_ID={client_id}",
                f"NAVIGATOR_ZOOM_CLIENT_SECRET={client_secret}",
                f"NAVIGATOR_ATTENDEE_PROJECT_NAME={env['NAVIGATOR_ATTENDEE_PROJECT_NAME']}",
                "python",
                "manage.py",
                "shell",
            ],
            input=script.read_text(encoding="utf-8"),
            cwd=compose_dir,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=env,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        print(f"[attendee] WARN: zoom credential sync skipped: {exc}", flush=True)
        return False

    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or "ATTENDEE_ZOOM_CREDENTIALS_OK" not in out:
        print(
            "[attendee] WARN: zoom credential sync failed — "
            f"run ./scripts/sync-attendee-zoom-credentials.sh\n{out.strip()[:500]}",
            flush=True,
        )
        return False

    print("[attendee] synced Zoom Meeting SDK creds into Attendee project", flush=True)
    return True


def ensure_webpage_streamer(*, compose_dir: Path | None = None) -> bool:
    """Bring the webpage-streamer back up before a demo arms screenshare.

    Attendee's streamer shuts itself down after 900s with no keepalive, i.e.
    whenever there is a gap between demos. Nothing restarts it, and the failure
    is silent: the screenshare PATCH still returns 200 and the meeting simply
    never sees a shared screen. Returns True when the service is running.
    """
    compose_dir = compose_dir or _compose_dir()
    if _in_pytest() or not compose_dir.is_dir():
        return False
    try:
        proc = subprocess.run(
            ["docker", "compose", "-f", _COMPOSE_FILES[0], "-f", _COMPOSE_FILES[1],
             "--profile", _COMPOSE_PROFILE, "up", "-d", _STREAMER_SERVICE],
            cwd=compose_dir,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        print(f"[attendee] WARN: streamer preflight skipped: {exc}", flush=True)
        return False
    if proc.returncode != 0:
        print(
            "[attendee] WARN: webpage-streamer not started — screenshare will be "
            f"blank: {(proc.stderr or proc.stdout or '').strip()[:300]}",
            flush=True,
        )
        return False
    print("[attendee] webpage-streamer up (screenshare renderer)", flush=True)
    return True


def ensure_attendee_stack(
    *,
    base_url: str | None = None,
    autostart: bool | None = None,
    compose_dir: Path | None = None,
    wait_timeout_s: float = 180.0,
) -> bool:
    """Start local Attendee via docker compose when needed. Returns True if reachable."""
    base_url = base_url or settings.attendee_base_url
    autostart = settings.attendee_autostart if autostart is None else autostart
    compose_dir = compose_dir or _compose_dir()

    if not autostart or _in_pytest() or not is_local_attendee_url(base_url):
        return attendee_reachable(base_url)

    if not compose_dir.is_dir():
        print(
            f"[attendee] WARN: {compose_dir} missing — clone attendee-labs/attendee "
            "or set NAVIGATOR_ATTENDEE_COMPOSE_DIR",
            flush=True,
        )
        return False

    _sync_attendee_override(compose_dir)
    recreate = _needs_voice_agent_recreate(compose_dir)
    if not recreate and attendee_reachable(base_url):
        print(f"[attendee] already up at {base_url}", flush=True)
        ensure_attendee_zoom_credentials(compose_dir=compose_dir)
        return True
    if recreate:
        print("[attendee] recreating stack (ENABLE_VOICE_AGENTS)…", flush=True)

    print(f"[attendee] starting docker stack in {compose_dir}…", flush=True)
    try:
        proc = _docker_compose_up(compose_dir, force_recreate=recreate)
    except FileNotFoundError as exc:
        print(f"[attendee] WARN: {exc}", flush=True)
        return False

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        print(
            f"[attendee] WARN: docker compose failed ({proc.returncode})"
            + (f": {detail}" if detail else ""),
            flush=True,
        )
        if "permission denied" in detail.lower() or "connect: permission denied" in detail.lower():
            print(
                "[attendee] docker permission denied — run ./scripts/fix-attendee-voice.sh "
                "on the host (or add user to docker group)",
                flush=True,
            )
        return False

    deadline = time.time() + wait_timeout_s
    while time.time() < deadline:
        if attendee_reachable(base_url):
            print(f"[attendee] ready at {base_url}", flush=True)
            ensure_attendee_zoom_credentials(compose_dir=compose_dir)
            return True
        time.sleep(2)

    print(
        f"[attendee] WARN: still unreachable at {base_url} after {wait_timeout_s:.0f}s — "
        "check `docker compose ps` in the Attendee clone",
        flush=True,
    )
    return False
