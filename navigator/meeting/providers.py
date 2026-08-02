"""Meeting providers: create a fresh join link per demo session.

Before this module the meeting URL was a deployment-wide env var
(``NAVIGATOR_MEETING_URL``), so two prospects could not be demoed at once and a
human had to mint a link before every run. A provider turns that into an API
call: one session, one meeting, created on demand.

Google Meet is the platform the live pipeline assumes (MeetSpeaker, Attendee's
``google_meet_use_login``), so it is the default.

Meet links come from ``spaces.create``, not from a calendar event. That is the
right primitive for a "Show Demo" button: a space exists the moment it is
created and is live immediately -- there is no start time to schedule, nothing
on anyone's calendar, and nothing to clean up afterwards. A Calendar-created
conference would be the opposite on both counts: it is an event at a time, and
its conference defaults to TRUSTED access, where an external bot knocks and
waits for a human to admit it. ``config.accessType=OPEN`` is what lets Navigator
walk into its own meeting alone, which is the whole design.

Credentials are org-wide env vars, deliberately: per-tenant credentials belong
in a vault, which is a separate piece of work.

ponytail: urllib for the API calls (same as attendee.py / email_notify.py),
google-auth only for signing the service-account JWT. No googleapiclient.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

Platform = Literal["google_meet", "zoom", "static"]

MEET_API = "https://meet.googleapis.com/v2/spaces"
ZOOM_TOKEN_API = "https://zoom.us/oauth/token"
ZOOM_API = "https://api.zoom.us/v2"

#: Creating a space is all we do. No calendar, no event, no stored scope.
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/meetings.space.created"]


class MeetingProviderError(RuntimeError):
    """Could not create a meeting. Never raised for a *joining* problem."""


@dataclass(frozen=True)
class MeetingInfo:
    """One created meeting. `url` is what Attendee joins and the prospect opens."""

    url: str
    platform: Platform
    provider_id: str
    """Meet space name / Zoom meeting id. For teardown + audit."""
    passcode: str = ""
    """Zoom only. Meet links carry their own access in the URL."""
    open_access: bool = False
    """True when anyone with the link joins directly -- i.e. bot-first works
    without a human admitting Navigator from the waiting room."""

    def public(self) -> dict:
        return {
            "url": self.url,
            "platform": self.platform,
            "provider_id": self.provider_id,
            "passcode": self.passcode,
            "open_access": self.open_access,
        }


class MeetingProvider(Protocol):
    platform: Platform

    def create_meeting(
        self, product_id: str, *, topic: str = ""
    ) -> MeetingInfo:
        ...


# -- http ---------------------------------------------------------------------


def _post(url: str, *, headers: dict[str, str], body: dict | str | None) -> dict:
    if isinstance(body, str):
        data: bytes | None = body.encode()
    elif body is None:
        data = None
    else:
        data = json.dumps(body).encode()
    req = Request(url, data=data, method="POST", headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:600]
        raise MeetingProviderError(f"{url} -> HTTP {exc.code}: {detail}") from None
    except Exception as exc:  # noqa: BLE001
        raise MeetingProviderError(f"{url} -> {exc!r}") from None


def _get(url: str, *, headers: dict[str, str]) -> dict:
    req = Request(url, method="GET", headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:600]
        raise MeetingProviderError(f"{url} -> HTTP {exc.code}: {detail}") from None
    except Exception as exc:  # noqa: BLE001
        raise MeetingProviderError(f"{url} -> {exc!r}") from None


# -- google -------------------------------------------------------------------


def _google_token(sa_json: str, impersonate: str) -> str:
    """Access token for a service account with domain-wide delegation.

    DWD is not optional: a bare service account has no Meet of its own, so it
    must impersonate a real Workspace user to create a space.
    """
    if not sa_json:
        raise MeetingProviderError(
            "NAVIGATOR_GOOGLE_SA_JSON is unset -- Google Meet needs a service "
            "account JSON (path or inline)"
        )
    if not impersonate:
        raise MeetingProviderError(
            "NAVIGATOR_GOOGLE_IMPERSONATE is unset -- domain-wide delegation "
            "must impersonate a Workspace user; a bare service account cannot "
            "create a Meet space"
        )
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise MeetingProviderError(f"google-auth not installed: {exc}") from None

    raw = sa_json.strip()
    try:
        info = (
            json.loads(raw)
            if raw.startswith("{")
            else json.loads(Path(raw).expanduser().read_text())
        )
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise MeetingProviderError(
            "NAVIGATOR_GOOGLE_SA_JSON is not valid JSON (use a single-line "
            f"value or a path to the key file): {exc}"
        ) from None
    try:
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=GOOGLE_SCOPES
        ).with_subject(impersonate)
        creds.refresh(GoogleRequest())
    except Exception as exc:  # noqa: BLE001
        raise MeetingProviderError(f"Google SA token failed: {exc}") from None
    return creds.token


class GoogleMeetProvider:
    """An instant Meet space per session, service account + domain-wide delegation.

    A space is live the moment it is created, so the prospect's click and the
    meeting starting are the same event. Nothing is scheduled.
    """

    platform: Platform = "google_meet"

    def __init__(self, *, sa_json: str, impersonate: str) -> None:
        self.sa_json = sa_json
        self.impersonate = impersonate

    def create_meeting(
        self, product_id: str, *, topic: str = ""
    ) -> MeetingInfo:
        """`topic` is ignored: a Meet space has no title, only a link."""
        token = _google_token(self.sa_json, self.impersonate)
        data = _post(
            MEET_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            # OPEN is load-bearing: anything else makes Navigator knock and wait.
            body={"config": {"accessType": "OPEN", "entryPointAccess": "ALL"}},
        )
        url = data.get("meetingUri") or ""
        if not url:
            raise MeetingProviderError(f"Meet spaces.create returned no URI: {data}")
        return MeetingInfo(
            url=url,
            platform="google_meet",
            provider_id=str(data.get("name") or data.get("meetingCode") or ""),
            open_access=True,
        )


# -- zoom ---------------------------------------------------------------------


class ZoomProvider:
    """Zoom meeting per session via a Server-to-Server OAuth app.

    The Attendee bot starts the meeting as host via ZAK (see ``fetch_zak`` and
    ``POST /v1/zoom/zak``). Prospects only ever get ``join_url``.
    """

    platform: Platform = "zoom"

    def __init__(
        self,
        *,
        account_id: str,
        client_id: str,
        client_secret: str,
        user_id: str = "me",
    ) -> None:
        self.account_id = account_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_id = user_id or "me"

    def _token(self) -> str:
        missing = [
            name
            for name, val in [
                ("NAVIGATOR_ZOOM_ACCOUNT_ID", self.account_id),
                ("NAVIGATOR_ZOOM_CLIENT_ID", self.client_id),
                ("NAVIGATOR_ZOOM_CLIENT_SECRET", self.client_secret),
            ]
            if not val
        ]
        if missing:
            raise MeetingProviderError(
                f"missing Zoom S2S credentials: {', '.join(missing)}"
            )
        import base64

        basic = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        data = _post(
            f"{ZOOM_TOKEN_API}?grant_type=account_credentials"
            f"&account_id={self.account_id}",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body="",
        )
        token = data.get("access_token")
        if not token:
            raise MeetingProviderError(f"Zoom token response has no token: {data}")
        return str(token)

    def fetch_zak(self, user_id: str | None = None) -> str:
        """Mint a short-lived ZAK so Attendee can start the meeting as host."""
        uid = user_id or self.user_id
        data = _get(
            f"{ZOOM_API}/users/{uid}/token?type=zak",
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        zak = data.get("token")
        if not zak:
            raise MeetingProviderError(f"Zoom ZAK response has no token: {data}")
        return str(zak)

    def create_meeting(
        self, product_id: str, *, topic: str = ""
    ) -> MeetingInfo:
        uid = self.user_id
        data = _post(
            f"{ZOOM_API}/users/{uid}/meetings",
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
            },
            body={
                "topic": topic or f"Navigator demo — {product_id}",
                # type 1 = instant. A scheduled meeting (type 2) would need a
                # start time, which is exactly what a "Show Demo" click has not
                # got: the meeting starts now or it is useless.
                "type": 1,
                "settings": {
                    # Host-first: Navigator starts the room via ZAK. Guests wait
                    # until the bot is host — if ZAK fails they would both hang
                    # forever with join_before_host=True and no host present.
                    "join_before_host": False,
                    "waiting_room": False,
                    "approval_type": 2,  # no registration
                },
            },
        )
        url = data.get("join_url") or ""
        if not url:
            raise MeetingProviderError(f"Zoom create returned no join_url: {data}")
        return MeetingInfo(
            url=str(url),
            platform="zoom",
            provider_id=str(data.get("id") or ""),
            passcode=str(data.get("password") or ""),
            open_access=True,
        )


# -- static -------------------------------------------------------------------


class StaticMeetingProvider:
    """The old behaviour, kept as an explicit choice: reuse one fixed link.

    For local runs against a Meet room you already opened by hand.
    """

    platform: Platform = "static"

    def __init__(self, url: str) -> None:
        self.url = url

    def create_meeting(
        self, product_id: str, *, topic: str = ""
    ) -> MeetingInfo:
        if not self.url:
            raise MeetingProviderError(
                "platform='static' needs NAVIGATOR_MEETING_URL to be set"
            )
        return MeetingInfo(
            url=self.url, platform="static", provider_id="static", open_access=False
        )


def make_provider(platform: Platform | None = None) -> MeetingProvider:
    """Build the configured provider. Import-time free of credentials."""
    from navigator.core.settings import settings

    choice = platform or settings.meeting_platform
    if choice == "google_meet":
        return GoogleMeetProvider(
            sa_json=settings.google_sa_json,
            impersonate=settings.google_impersonate,
        )
    if choice == "zoom":
        return ZoomProvider(
            account_id=settings.zoom_account_id,
            client_id=settings.zoom_client_id,
            client_secret=settings.zoom_client_secret,
            user_id=settings.zoom_user_id,
        )
    if choice == "static":
        return StaticMeetingProvider(settings.meeting_url)
    raise MeetingProviderError(f"unknown meeting platform: {choice!r}")
