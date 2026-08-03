"""Single-use tickets for the exploration WebSocket.

The browser WebSocket API cannot send an Authorization header, so the dashboard
exchanges its JWT (on an authed POST route) for a short-lived ticket carried in
the query string. In-memory on purpose: exploration is already a single-process,
loopback-only dashboard feature, so there is nothing to share across workers.
"""

from __future__ import annotations

import secrets
import threading
import time

TTL_S = 60.0

_lock = threading.Lock()
_tickets: dict[str, tuple[str, float]] = {}


def mint_ticket(product_id: str) -> str:
    ticket = f"xpl_{secrets.token_urlsafe(24)}"
    with _lock:
        _prune()
        _tickets[ticket] = (product_id, time.monotonic() + TTL_S)
    return ticket


def redeem_ticket(ticket: str) -> str | None:
    """The product_id, consuming the ticket. None if unknown or expired."""
    if not ticket:
        return None
    with _lock:
        _prune()
        entry = _tickets.pop(ticket, None)
    if entry is None:
        return None
    product_id, expires = entry
    return product_id if time.monotonic() < expires else None


def _prune() -> None:
    now = time.monotonic()
    for key in [k for k, (_, exp) in _tickets.items() if exp <= now]:
        _tickets.pop(key, None)
