"""Notify a human of the Meet link via mailto: (opens local mail client).

ponytail: least-setup share. Ceiling: opens a *draft* — you must click Send.
If no mail app is wired, we still print the Meet link to the terminal.
Upgrade: SMTP/Graph/Resend for true auto-send into the inbox.
"""

from __future__ import annotations

import shutil
import subprocess
import webbrowser
from urllib.parse import quote


def build_mailto_url(
    *,
    to: str,
    meeting_url: str,
    subject: str | None = None,
    body: str | None = None,
) -> str:
    subj = subject or "Navigator demo — join the meeting"
    text = body or _default_body(meeting_url)
    if meeting_url not in text:
        text = f"{text.rstrip()}\n\n{meeting_url}\n"
    return (
        f"mailto:{to}"
        f"?subject={quote(subj)}"
        f"&body={quote(text)}"
    )


def _default_body(meeting_url: str) -> str:
    return (
        "Navigator AI is starting a live product demo.\n\n"
        f"Join here: {meeting_url}\n"
    )


def notify_demo_link_mailto(
    *,
    to: str,
    meeting_url: str,
    subject: str | None = None,
    body: str | None = None,
) -> str:
    """Open a mail *draft* with the Meet link. Does not auto-send.

    Returns the mailto URL. Always prints the Meet link so it is not lost if
    the mail client fails to open.
    """
    subj = subject or "Navigator demo — join the meeting"
    text = body or _default_body(meeting_url)
    url = build_mailto_url(to=to, meeting_url=meeting_url, subject=subj, body=text)

    print("=" * 60, flush=True)
    print("[notify] mailto does NOT auto-send — it opens a draft.", flush=True)
    print(f"[notify] To:      {to}", flush=True)
    print(f"[notify] Subject: {subj}", flush=True)
    print(f"[notify] Meet:    {meeting_url}", flush=True)
    print("[notify] Copy the Meet link above if no mail window appears.", flush=True)
    print("=" * 60, flush=True)

    opened = False
    xdg = shutil.which("xdg-email")
    if xdg:
        try:
            subprocess.run(
                [
                    xdg,
                    "--utf8",
                    "--subject",
                    subj,
                    "--body",
                    text,
                    to,
                ],
                check=False,
                timeout=15,
            )
            opened = True
            print("[notify] opened draft via xdg-email — click Send in the mail app", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] xdg-email failed: {exc}", flush=True)

    if not opened:
        try:
            webbrowser.open(url)
            print("[notify] opened draft via webbrowser — click Send", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] webbrowser mailto failed: {exc}", flush=True)

    return url
