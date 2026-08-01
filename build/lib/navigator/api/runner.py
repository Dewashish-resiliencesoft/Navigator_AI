"""Demo lifecycle: one running demo per call, isolated from every other.

Isolation is the whole job of this module. Each demo gets:

  - its own Playwright **browser context**, not just its own page. Two demos of two
    different products must not share cookies, localStorage, or an auth session.
    A page-per-demo would leak all three.
  - its own CallDeps, so no graph node can see another demo's state.
  - its own thread. The Playwright sync API is not thread-safe across threads, so a
    demo owns its playwright instance start-to-finish rather than borrowing a
    shared one.

The registry of live demos is in-process, which is correct for a single-worker
deployment and wrong the moment there are two. That boundary is marked below.
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from playwright.sync_api import sync_playwright

from navigator.agent.graph import build_graph
from navigator.agent.state import CallDeps, initial_state
from navigator.config.site_graph import SiteGraph
from navigator.logs.store import ActionLog
from navigator.voice.tts import PrintSpeaker, Speaker

DemoStatus = Literal["starting", "running", "finished", "failed"]


@dataclass
class DemoHandle:
    """A demo's observable state. Read by GET /v1/demos/{id}."""

    demo_id: UUID
    product_id: str
    revision: int
    session_id: UUID
    status: DemoStatus = "starting"
    page_id: str = ""
    """Where in the site graph the agent currently is."""
    actions: int = 0
    failures: int = 0
    error: str | None = None
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    finished_at: datetime | None = None
    said: list[str] = field(default_factory=list)
    """Narration so far, for a live transcript view."""

    _thread: threading.Thread | None = field(default=None, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict:
        """Serialisable view. Excludes the thread and the stop event."""
        return {
            k: v
            for k, v in self.__dict__.items()
            if not k.startswith("_")
        }


class _RecordingSpeaker:
    """Wraps a Speaker so a demo's narration is queryable while it runs."""

    def __init__(self, inner: Speaker, handle: DemoHandle) -> None:
        self._inner = inner
        self._handle = handle

    def say(self, text: str) -> None:
        self._handle.said.append(text)
        self._inner.say(text)


class DemoRunner:
    """Starts demos and tracks the live ones.

    TODO(phase 5+): the `_demos` dict is per-process. A multi-worker deployment
    needs this in Redis (or the demo pinned to a worker), otherwise
    GET /v1/demos/{id} hits a worker that has never heard of the demo. Single
    worker is fine until it isn't; `uvicorn --workers 1` is not a suggestion.
    """

    def __init__(
        self,
        db_path: str,
        headful: bool = False,
        archive_dir: str | Path = "archives",
    ) -> None:
        self.db_path = db_path
        self.headful = headful
        self.archive_dir = Path(archive_dir)
        self._demos: dict[UUID, DemoHandle] = {}
        self._lock = threading.Lock()

    def start(
        self,
        product_id: str,
        graph: SiteGraph,
        revision: int,
        flow: tuple[str, str],
        speaker: Speaker | None = None,
    ) -> DemoHandle:
        handle = DemoHandle(
            demo_id=uuid4(),
            product_id=product_id,
            revision=revision,
            session_id=uuid4(),
            page_id=flow[0],
        )
        with self._lock:
            self._demos[handle.demo_id] = handle

        thread = threading.Thread(
            target=self._run,
            args=(handle, graph, flow, speaker or PrintSpeaker()),
            name=f"demo-{handle.demo_id}",
            daemon=True,
        )
        handle._thread = thread
        thread.start()
        return handle

    def get(self, demo_id: UUID, product_id: str | None = None) -> DemoHandle | None:
        """A demo by id, scoped to a product so one tenant can't read another's."""
        handle = self._demos.get(demo_id)
        if handle is None:
            return None
        if product_id is not None and handle.product_id != product_id:
            return None
        return handle

    def list(self, product_id: str) -> list[DemoHandle]:
        return [h for h in self._demos.values() if h.product_id == product_id]

    def stop(self, demo_id: UUID, product_id: str | None = None) -> DemoHandle | None:
        """Ask a demo to wind down. Cooperative -- the graph finishes its step."""
        handle = self.get(demo_id, product_id)
        if handle is not None:
            handle._stop.set()
        return handle

    def wait(self, demo_id: UUID, timeout: float = 60.0) -> DemoHandle | None:
        """Block until a demo finishes. For tests and synchronous callers."""
        handle = self._demos.get(demo_id)
        if handle is None or handle._thread is None:
            return handle
        handle._thread.join(timeout)
        return handle

    # -- the worker ----------------------------------------------------------

    def _run(
        self,
        handle: DemoHandle,
        graph: SiteGraph,
        flow: tuple[str, str],
        speaker: Speaker,
    ) -> None:
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=not self.headful)
                # A fresh context per demo: separate cookie jar, storage, and
                # session. This is the tenant boundary in the browser.
                context = browser.new_context()
                page = context.new_page()
                try:
                    with ActionLog(self.db_path) as log:
                        deps = CallDeps(
                            graph=graph,
                            page=page,
                            log=log,
                            speaker=_RecordingSpeaker(speaker, handle),
                            scripted_flow=flow,
                            product_id=handle.product_id,
                            archive_dir=self.archive_dir,
                        )
                        handle.status = "running"
                        final = build_graph(deps).invoke(
                            initial_state(handle.session_id, flow[0])
                        )
                        handle.page_id = final.get("page_id", handle.page_id)
                        handle.actions = len(final.get("entries", []))
                        handle.failures = len(final.get("failures", []))
                        handle.status = "finished"
                finally:
                    context.close()
                    browser.close()
        except Exception:
            handle.status = "failed"
            handle.error = traceback.format_exc(limit=3)
        finally:
            handle.finished_at = datetime.now(timezone.utc)
