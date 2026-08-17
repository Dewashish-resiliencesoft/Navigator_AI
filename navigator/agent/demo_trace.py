"""Structured diagnostics for demo engine and narration/action timing."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any


def emit_demo_trace(
    trace: Callable[[dict[str, Any]], None] | None,
    *,
    session_id: object,
    product_id: str,
    event: str,
    **fields: Any,
) -> None:
    payload: dict[str, Any] = {
        "event": event,
        "session_id": str(session_id),
        "product_id": product_id,
        "wall_time": time.time(),
        **fields,
    }
    if trace is not None:
        try:
            trace(payload)
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[demo-trace] sink failed: {exc}", flush=True)
    print(f"[demo-trace] {json.dumps(payload, sort_keys=True)}", flush=True)


def emit_sync_trace(
    trace: Callable[[dict[str, Any]], None] | None,
    *,
    session_id: object,
    product_id: str,
    engine: str,
    flow_id: str,
    step: int,
    narration_started_ns: int,
    action_started_ns: int,
) -> None:
    emit_demo_trace(
        trace,
        session_id=session_id,
        product_id=product_id,
        event="narration_action_sync",
        engine=engine,
        flow_id=flow_id,
        step=step,
        narration_started_ns=narration_started_ns,
        action_started_ns=action_started_ns,
        gap_ms=round((action_started_ns - narration_started_ns) / 1_000_000, 3),
    )
