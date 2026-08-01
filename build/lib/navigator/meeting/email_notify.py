"""Auto-send Meet link email via Resend (free tier).

Sign up: https://resend.com → API Keys → copy key.
Free tier: send from ``beth.t@example.com`` to the email you signed up with
(or verify a domain later for arbitrary recipients).

ponytail: one HTTP POST, no SDK. Ceiling: Resend free limits / from-address
rules. Upgrade: own domain + SMTP/Graph.
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RESEND_API = "https://api.resend.com/emails"
DEFAULT_FROM = "Navigator AI <onboarding@resend.dev>"


def send_meet_link_email(
    *,
    api_key: str,
    to: str,
    meeting_url: str,
    from_addr: str = DEFAULT_FROM,
    subject: str | None = None,
) -> str:
    """Send the Meet link. Returns Resend email id."""
    if not api_key:
        raise RuntimeError("Resend API key missing (NAVIGATOR_RESEND_API_KEY)")
    if not to:
        raise RuntimeError("notify email missing (NAVIGATOR_NOTIFY_EMAIL)")

    subj = subject or "Navigator demo — join the meeting"
    text = (
        "Navigator AI is starting a live product demo.\n\n"
        f"Join here: {meeting_url}\n"
    )
    html = (
        "<p>Navigator AI is starting a live product demo.</p>"
        f'<p><a href="{meeting_url}">Join the meeting</a></p>'
        f"<p>{meeting_url}</p>"
    )
    payload = {
        "from": from_addr,
        "to": [to],
        "subject": subj,
        "text": text,
        "html": html,
    }
    req = Request(
        RESEND_API,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Cloudflare on api.resend.com rejects bare urllib UA (1010).
            "User-Agent": "NavigatorAI/1.0 (+https://github.com/navigator-ai; email)",
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read() or b"{}"
    except HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Resend send failed HTTP {e.code}: {detail}") from e
    except URLError as e:
        raise RuntimeError(f"Resend unreachable: {e}") from e

    data = json.loads(raw) if raw else {}
    email_id = str(data.get("id") or "")
    if not email_id:
        raise RuntimeError(f"Resend returned no id: {data!r}")
    return email_id
