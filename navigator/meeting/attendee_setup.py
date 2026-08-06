"""Push local .env values into the self-hosted Attendee stack before a live demo.

Attendee stores Zoom *Meeting SDK* credentials in its DB (encrypted). Navigator
uses separate *Server-to-Server OAuth* creds to create meetings and mint ZAK.
When both live in ``.env``, we sync the SDK pair into Attendee automatically so
you do not re-enter them in the Attendee dashboard.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from navigator.core.settings import settings


class AttendeeSetupError(RuntimeError):
    """Could not sync credentials into Attendee."""


def _attendee_zoom_sdk_credentials() -> tuple[str, str]:
    """Meeting SDK pair for Attendee web bot JWT (General OAuth + Embed SDK).

    S2S OAuth creds (``NAVIGATOR_ZOOM_CLIENT_*``) cannot sign Meeting SDK JWTs
    and produce Zoom error 3712 (Signature is invalid).
    """
    client_id = (settings.attendee_zoom_client_id or "").strip()
    client_secret = (settings.attendee_zoom_client_secret or "").strip()
    return client_id, client_secret


def _resolve_attendee_compose_dir() -> Path | None:
    raw = settings.attendee_compose_dir
    if raw and Path(raw).is_dir():
        return Path(raw)
    candidates = [
        Path.home() / "projects" / "attendee",
        Path(__file__).resolve().parents[3].parent / "attendee",
    ]
    for path in candidates:
        if (path / "dev.docker-compose.yaml").is_file():
            return path
    return None


def sync_attendee_zoom_credentials(*, project_name: str = "Navigator") -> None:
    """Write Zoom Meeting SDK OAuth creds from ``.env`` into Attendee's store."""
    client_id, client_secret = _attendee_zoom_sdk_credentials()
    if not client_id or not client_secret:
        raise AttendeeSetupError(
            "missing Zoom Meeting SDK credentials for Attendee — set "
            "NAVIGATOR_ATTENDEE_ZOOM_CLIENT_ID + NAVIGATOR_ATTENDEE_ZOOM_CLIENT_SECRET "
            "from a General OAuth app with Features → Embed → Meeting SDK enabled "
            "(marketplace.zoom.us). Do not use the Server-to-Server OAuth app — "
            "that one only creates meetings; Attendee needs the Meeting SDK app "
            "for join (error 3712 Signature is invalid otherwise)."
        )

    compose_dir = _resolve_attendee_compose_dir()
    if compose_dir is None:
        raise AttendeeSetupError(
            "Attendee repo not found — set NAVIGATOR_ATTENDEE_COMPOSE_DIR to the "
            "directory containing dev.docker-compose.yaml (e.g. ~/projects/attendee)"
        )

    creds_json = json.dumps({"client_id": client_id, "client_secret": client_secret})
    project_name_json = json.dumps(project_name)
    py = f"""
from bots.models import Project, Credentials
p = Project.objects.filter(name={project_name_json}).first() or Project.objects.order_by("id").first()
if p is None:
    raise SystemExit("no Attendee project — sign up at http://localhost:8002 first")
cred, _ = Credentials.objects.get_or_create(
    project=p, credential_type=Credentials.CredentialTypes.ZOOM_OAUTH
)
cred.set_credentials({creds_json})
print("attendee_zoom_sync_ok", p.object_id)
"""

    cmd = [
        "docker",
        "compose",
        "-f",
        "dev.docker-compose.yaml",
        "-f",
        "local.docker-compose.yaml",
        "exec",
        "-T",
        "attendee-app-local",
        "python",
        "manage.py",
        "shell",
        "-c",
        py,
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=compose_dir,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AttendeeSetupError(
            "docker not found — install Docker Desktop and ensure Attendee stack is up"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AttendeeSetupError("timed out syncing Zoom creds into Attendee") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:800]
        raise AttendeeSetupError(
            f"Attendee Zoom credential sync failed (exit {proc.returncode}): {detail}"
        )

    print(
        "[live] Attendee Zoom Meeting SDK creds synced from NAVIGATOR_ATTENDEE_ZOOM_CLIENT_*",
        flush=True,
    )
