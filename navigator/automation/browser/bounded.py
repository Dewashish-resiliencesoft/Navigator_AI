"""Run one awaitable with a hard limit without nesting onto Playwright's loop."""

from __future__ import annotations

import asyncio
import threading


def run_limited(coro, timeout: float):
    """Await ``coro`` for at most ``timeout`` seconds.

    ``asyncio.wait_for`` cancels the awaitable when the limit hits, so a
    stub that sleeps forever stops and the caller continues.
    """

    async def _go():
        return await asyncio.wait_for(coro, timeout)

    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False
    if not running:
        return asyncio.run(_go())

    box: dict = {}

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            box["value"] = loop.run_until_complete(_go())
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join(timeout + 1.0)
    if "error" in box:
        raise box["error"]
    if "value" not in box:
        raise TimeoutError("bounded await did not finish")
    return box["value"]
