"""Sync Navigator Zoom OAuth app creds into Attendee (manage.py shell).

Run:
  docker compose -f dev.docker-compose.yaml -f local.docker-compose.yaml \
    exec -T attendee-app-local env NAVIGATOR_ZOOM_CLIENT_ID=... \
    NAVIGATOR_ZOOM_CLIENT_SECRET=... python manage.py shell < bootstrap_attendee_zoom.py
"""

from __future__ import annotations

import os

from bots.models import Credentials, Project

PROJECT_NAME = (os.environ.get("NAVIGATOR_ATTENDEE_PROJECT_NAME") or "Navigator").strip()
CLIENT_ID = (os.environ.get("NAVIGATOR_ZOOM_CLIENT_ID") or "").strip()
CLIENT_SECRET = (os.environ.get("NAVIGATOR_ZOOM_CLIENT_SECRET") or "").strip()

if not CLIENT_ID or not CLIENT_SECRET:
    raise SystemExit(
        "missing NAVIGATOR_ZOOM_CLIENT_ID / NAVIGATOR_ZOOM_CLIENT_SECRET in env"
    )

project = Project.objects.filter(name=PROJECT_NAME).first()
if project is None:
    raise SystemExit(
        f"Attendee project {PROJECT_NAME!r} not found — run bootstrap_local.py first"
    )

cred, _ = Credentials.objects.get_or_create(
    project=project,
    credential_type=Credentials.CredentialTypes.ZOOM_OAUTH,
)
cred.set_credentials({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
print("ATTENDEE_ZOOM_CREDENTIALS_OK")
print(f"project={project.object_id}")
