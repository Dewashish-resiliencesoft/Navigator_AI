"""Teams Incoming Webhook notifier."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def notify_demo_link(
    *,
    webhook_url: str,
    meeting_url: str,
    message: str | None = None,
) -> None:
    """Post the Meet link to a Teams channel via Incoming Webhook."""
    text = message or f"Navigator demo starting — join: {meeting_url}"
    if meeting_url not in text:
        text = f"{text}\n{meeting_url}"
    req = Request(
        webhook_url,
        data=json.dumps({"text": text}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=30) as resp:
            status = getattr(resp, "status", 200)
            if status >= 300:
                raise RuntimeError(f"Teams webhook HTTP {status}")
    except HTTPError as e:
        raise RuntimeError(
            f"Teams webhook failed: {e.code} {e.read().decode(errors='replace')}"
        ) from e
    except URLError as e:
        raise RuntimeError(f"Teams webhook unreachable: {e}") from e
