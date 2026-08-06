"""Windows-safe stdout (cp1252 consoles cannot encode all Unicode)."""

from __future__ import annotations


def safe_print(msg: str, *, flush: bool = True) -> None:
    """Print without raising UnicodeEncodeError on narrow Windows consoles."""
    try:
        print(msg, flush=flush)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="backslashreplace").decode("ascii"), flush=flush)
