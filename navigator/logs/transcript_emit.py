"""Best-effort meeting transcript writes — never break a live demo."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import UUID

from navigator.core.settings import settings
from navigator.logs.transcript import MeetingTranscriptStore


def emit_transcript(
    *,
    product_id: str,
    session_id: UUID | str,
    kind: str,
    text: str,
    page_id: str = "",
    db_path: str | Path | None = None,
) -> None:
    if not (text or "").strip():
        return
    path = db_path or settings.db_path
    try:
        with MeetingTranscriptStore(path) as store:
            store.append(
                product_id=product_id,
                session_id=session_id,
                kind=kind,
                page_id=page_id,
                text=text,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[transcript] emit failed: {exc}", flush=True)


def classify_agent_kind(
    session_id: UUID | str,
    product_id: str,
    *,
    db_path: str | Path | None = None,
) -> Literal["agent", "agent_reply"]:
    path = db_path or settings.db_path
    try:
        with MeetingTranscriptStore(path) as store:
            rows = store.for_session(session_id, product_id=product_id)
        if rows and rows[-1].kind == "visitor":
            return "agent_reply"
    except Exception:  # noqa: BLE001
        pass
    return "agent"
