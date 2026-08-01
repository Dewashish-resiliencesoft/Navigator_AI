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
from typing import Callable, Literal
from uuid import UUID, uuid4

from playwright.sync_api import sync_playwright

from navigator.agent.graph import build_graph
from navigator.agent.state import CallDeps, initial_state
from navigator.knowledge.site_graph import SiteGraph
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
    meeting_url: str | None = None
    """The link created for *this* session. None for a headless local demo."""
    platform: str | None = None
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    finished_at: datetime | None = None
    said: list[str] = field(default_factory=list)
    """Narration so far, for a live transcript view."""
    bot_id: str | None = None
    """Attendee bot id once joined — End uses this to leave the meeting."""
    bot_in_meeting: bool = False
    """True only after Attendee reports joined — safe to share join link."""

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

    def start_live(
        self,
        product_id: str,
        graph: SiteGraph,
        revision: int,
        flow: tuple[str, str],
        *,
        meeting_url: str,
        platform: str,
        run: Callable[..., str] | None = None,
        **kwargs,
    ) -> DemoHandle:
        """Run the *real* Meet pipeline: Attendee bot, tunnel, screenshare, agent.

        Same handle, thread, and tenant scoping as `start()`; only the worker
        differs. `run` is injectable so tests never touch Attendee or Playwright.
        """
        handle = DemoHandle(
            demo_id=uuid4(),
            product_id=product_id,
            revision=revision,
            session_id=uuid4(),
            page_id=flow[0],
            meeting_url=meeting_url,
            platform=platform,
        )
        with self._lock:
            self._demos[handle.demo_id] = handle

        thread = threading.Thread(
            target=self._run_live,
            args=(handle, graph, flow, run, kwargs),
            name=f"live-demo-{handle.demo_id}",
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

    def stop(
        self,
        demo_id: UUID,
        product_id: str | None = None,
        *,
        leave_bot: Callable[[str], None] | None = None,
    ) -> DemoHandle | None:
        """End a demo: signal stop and leave the Attendee bot if present.

        Live demos ignore `_stop` alone unless the worker polls it; leaving the
        bot is what actually kicks Navigator out of the meeting.
        """
        handle = self.get(demo_id, product_id)
        if handle is None:
            return None
        handle._stop.set()
        if handle.bot_id:
            leave = leave_bot or self._leave_attendee_bot
            try:
                leave(handle.bot_id)
            except Exception as exc:  # noqa: BLE001
                print(f"[runner] end: leave bot {handle.bot_id} failed: {exc}", flush=True)
        # UI End must free Start immediately — don't wait for worker teardown.
        if handle.status in ("starting", "running"):
            handle.status = "finished"
            handle.error = None
            handle.finished_at = datetime.now(timezone.utc)
        return handle

    @staticmethod
    def _leave_attendee_bot(bot_id: str) -> None:
        from navigator.meeting.attendee import AttendeeClient
        from navigator.core.settings import settings

        AttendeeClient(settings.attendee_base_url, settings.attendee_api_key).leave(
            bot_id
        )

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

    def _run_live(
        self,
        handle: DemoHandle,
        graph: SiteGraph,
        flow: tuple[str, str],
        run: Callable[..., str] | None,
        kwargs: dict,
    ) -> None:
        def on_bot_joined(bot_id: str) -> None:
            handle.bot_id = bot_id

        def on_meeting_ready(_url: str) -> None:
            handle.bot_in_meeting = True
            handle.said.append("Navigator is in the meeting — join link ready.")

        try:
            if run is None:
                from navigator.meeting.live_demo import run_live_meet_demo

                run = run_live_meet_demo
            handle.status = "running"
            run(
                meeting_url=handle.meeting_url,
                graph_cfg=graph,
                product_id=handle.product_id,
                session_id=handle.session_id,
                page_id=flow[0],
                flow_id=flow[1],
                headful=self.headful,
                # An API-started demo has no TTY and no local browser to open.
                interactive_listen=False,
                open_meet_in_browser=False,
                **kwargs,
                stop_event=handle._stop,
                on_bot_joined=on_bot_joined,
                on_meeting_ready=on_meeting_ready,
            )
            handle.status = "finished"
        except Exception as exc:
            # Operator End sets _stop; teardown noise must not mark failed.
            from navigator.meeting.live_demo import LiveDemoStopped

            if handle._stop.is_set() or isinstance(exc, LiveDemoStopped):
                handle.status = "finished"
                handle.error = None
            else:
                handle.status = "failed"
                handle.error = traceback.format_exc(limit=3)
        finally:
            handle.finished_at = datetime.now(timezone.utc)
            with ActionLog(self.db_path) as log:
                entries = log.entries(handle.session_id, product_id=handle.product_id)
            handle.actions = len(entries)
            handle.failures = sum(
                1 for e in entries if not (e.verify and e.verify.passed)
            )
