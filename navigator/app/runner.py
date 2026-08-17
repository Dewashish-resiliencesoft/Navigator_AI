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
import time
from typing import Callable, Literal
from uuid import UUID, uuid4

from playwright.sync_api import sync_playwright

from navigator.agent.graph import build_graph
from navigator.agent.state import CallDeps, initial_state
from navigator.knowledge.site_graph import SiteGraph
from navigator.logs.store import ActionLog
from navigator.voice.tts import PrintSpeaker, Speaker

DemoStatus = Literal["starting", "running", "finished", "failed"]

DemoOrigin = Literal["dashboard_test", "public_embed"]
"""Who started this demo, and therefore what it is for.

`dashboard_test` -- a Client validating their own setup from the dashboard.
Never billable, may run an unpublished draft revision.
`public_embed` -- an End User on the Client's landing page. The real product:
billable, and pinned to the published revision.

Set from the credential type at the auth boundary, never from a request body.
See docs/PRODUCT_MODEL.md.
"""

#: Poll-window transcript. Older lines drop; GET after this window still has the tail.
_MAX_SAID = 80
#: Keep finished handles for dashboard poll, then drop so `_demos` cannot grow forever.
KEEP_FINISHED_S = 120.0


@dataclass
class DemoHandle:
    """A demo's observable state. Read by GET /v1/demos/{id}."""

    demo_id: UUID
    product_id: str
    revision: int
    session_id: UUID
    origin: DemoOrigin
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
    leave_grace_remaining: int | None = None
    """Seconds before auto-ending after the human leaves the meeting."""

    _thread: threading.Thread | None = field(default=None, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def append_said(self, text: str) -> None:
        """Transcript cap so finished handles cannot grow without bound."""
        self.said.append(text)
        extra = len(self.said) - _MAX_SAID
        if extra > 0:
            del self.said[:extra]

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
        self._handle.append_said(text)
        self._inner.say(text)


class DemoRunner:
    """Starts demos and tracks the live ones.

    Note: The `_demos` dict is a fast local in-memory cache for the API to poll.
    In a multi-worker deployment (`uvicorn --workers N`), this local state is
    kept synchronized across all processes via the Redis PubSub loop (`_sync_loop`).
    """

    def __init__(
        self,
        db_path: str,
        headful: bool = False,
        archive_dir: str | Path = "archives",
        redis_url: str | None = None,
    ) -> None:
        self.db_path = db_path
        self.headful = headful
        self.archive_dir = Path(archive_dir)
        self._demos: dict[UUID, DemoHandle] = {}
        self._lock = threading.Lock()
        
        self.worker_id = str(uuid4())
        from navigator.app.state import DemoStateStore
        self._store = DemoStateStore(redis_url, self.worker_id, self._on_remote_stop)
        threading.Thread(target=self._sync_loop, daemon=True).start()

    def _on_remote_stop(self, demo_id: UUID) -> None:
        handle = self._demos.get(demo_id)
        if handle:
            handle._stop.set()

    def _sync_loop(self) -> None:
        while True:
            with self._lock:
                running = [
                    h
                    for h in self._demos.values()
                    if h.status in ("starting", "running")
                ]
            for h in running:
                self._store.save(h)
            self._reap_finished()
            time.sleep(1.0)

    def _reap_finished(self, *, keep_s: float = KEEP_FINISHED_S) -> int:
        """Drop finished/failed handles older than keep_s. Returns how many."""
        now = datetime.now(timezone.utc)
        drop: list[DemoHandle] = []
        with self._lock:
            for h in list(self._demos.values()):
                if h.status not in ("finished", "failed") or h.finished_at is None:
                    continue
                alive = h._thread is not None and h._thread.is_alive()
                if alive:
                    continue
                age = (now - h.finished_at).total_seconds()
                if age > keep_s:
                    self._demos.pop(h.demo_id, None)
                    drop.append(h)
        for h in drop:
            self._store.drop(h)
        return len(drop)

    def start(
        self,
        product_id: str,
        graph: SiteGraph,
        revision: int,
        flow: tuple[str, str],
        speaker: Speaker | None = None,
        *,
        origin: DemoOrigin,
    ) -> DemoHandle:
        handle = DemoHandle(
            demo_id=uuid4(),
            product_id=product_id,
            revision=revision,
            session_id=uuid4(),
            origin=origin,
            page_id=flow[0],
        )
        with self._lock:
            self._demos[handle.demo_id] = handle
        self._store.save(handle)
        self._store.set_owner(handle.demo_id)

        thread = threading.Thread(
            target=self._run,
            args=(handle, graph, flow, speaker or PrintSpeaker()),
            name=f"demo-{handle.demo_id}",
            daemon=True,
        )
        handle._thread = thread
        thread.start()
        self._persist_run(handle)
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
        origin: DemoOrigin,
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
            origin=origin,
            page_id=flow[0],
            meeting_url=meeting_url,
            platform=platform,
        )
        with self._lock:
            self._demos[handle.demo_id] = handle
        self._store.save(handle)
        self._store.set_owner(handle.demo_id)

        thread = threading.Thread(
            target=self._run_live,
            args=(handle, graph, flow, run, kwargs),
            name=f"live-demo-{handle.demo_id}",
            daemon=True,
        )
        handle._thread = thread
        thread.start()
        self._persist_run(handle)
        return handle

    def get(self, demo_id: UUID, product_id: str | None = None) -> DemoHandle | None:
        """A demo by id, scoped to a product so one tenant can't read another's."""
        handle = self._demos.get(demo_id)
        if handle is None:
            handle = self._store.get(demo_id)
        if handle is None:
            return None
        if product_id is not None and handle.product_id != product_id:
            return None
        return handle

    def list(self, product_id: str) -> list[DemoHandle]:
        remote = {h.demo_id: h for h in self._store.list(product_id)}
        local = {h.demo_id: h for h in self._demos.values() if h.product_id == product_id}
        remote.update(local)
        return list(remote.values())

    @staticmethod
    def _finalize_live_demo(handle: DemoHandle, *, operator_stopped: bool = False) -> None:
        """Clear meeting flag and append a terminal transcript line once."""
        handle.bot_in_meeting = False
        tail = [s.lower() for s in handle.said[-3:]]
        if any(
            t.startswith(("demo ended", "demo completed", "demo failed")) for t in tail
        ):
            return
        if operator_stopped or handle._stop.is_set():
            handle.append_said("Demo ended — meeting and browser closed.")
        elif handle.status == "failed":
            handle.append_said("Demo failed — meeting and browser closed.")
        else:
            handle.append_said(
                f"Demo completed — {handle.actions} actions, "
                f"{handle.failures} failures."
            )

    def stop(
        self,
        demo_id: UUID,
        product_id: str | None = None,
        *,
        leave_bot: Callable[[str], None] | None = None,
    ) -> DemoHandle | None:
        handle = self.get(demo_id, product_id)
        if handle is None:
            return None

        owner = self._store.get_owner(demo_id)
        if owner and owner != self.worker_id:
            self._store.publish_stop(owner, demo_id)
        else:
            if demo_id in self._demos:
                self._demos[demo_id]._stop.set()

        if handle.bot_id:
            leave = leave_bot or self._leave_attendee_bot
            try:
                leave(handle.bot_id)
            except Exception as exc:  # noqa: BLE001
                print(f"[runner] end: leave bot {handle.bot_id} failed: {exc}", flush=True)

        # UI End must free Start immediately — don't wait for worker teardown.
        if handle.status in ("starting", "running"):
            self._finalize_live_demo(handle, operator_stopped=True)
            handle.leave_grace_remaining = None
            handle.status = "finished"
            handle.error = None
            handle.finished_at = datetime.now(timezone.utc)
            self._store.save(handle)
            self._persist_run(handle)
        return handle

    def _persist_run(self, handle: DemoHandle, *, browser: str = "") -> None:
        """Best-effort demo_runs upsert — never kill the demo thread on DB errors."""
        try:
            from navigator.logs.host_meta import capture_host_meta, meeting_label

            meta = capture_host_meta()
            if browser:
                meta["browser"] = browser
            with ActionLog(self.db_path) as log:
                log.upsert_run(
                    session_id=handle.session_id,
                    demo_id=handle.demo_id,
                    product_id=handle.product_id,
                    platform=handle.platform or "local",
                    status=handle.status,
                    origin=handle.origin,
                    meeting_label=meeting_label(handle.meeting_url, handle.platform),
                    started_at=handle.started_at,
                    ended_at=handle.finished_at,
                    **meta,
                )
                log.prune_runs(days=7)
        except Exception as exc:  # noqa: BLE001
            print(f"[runner] persist demo_run failed: {exc}", flush=True)

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
                        from navigator.agent.demo_trace import emit_demo_trace

                        emit_demo_trace(
                            None,
                            session_id=handle.session_id,
                            product_id=handle.product_id,
                            event="engine_selected",
                            engine="langgraph",
                            reason="headless DemoRunner._run",
                            live_agent_present=False,
                            playlist_demo=bool(graph.demo_playlist),
                            timeline_ready=False,
                            conversational=False,
                        )
                        handle.status = "running"
                        self._persist_run(handle)
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
            self._persist_run(handle)
            self._store.save(handle)

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
            handle.append_said("Navigator is in the meeting — join link ready.")

        def on_leave_grace(remaining: int | None) -> None:
            handle.leave_grace_remaining = remaining

        try:
            if run is None:
                from navigator.meeting.live_demo import run_live_meet_demo

                run = run_live_meet_demo
            handle.status = "running"
            self._persist_run(handle)
            # Dashboard static admit-flow may pass open_meet_in_browser=True;
            # default False so API workers don't pop a browser on the server.
            open_browser = bool(kwargs.pop("open_meet_in_browser", False))
            live_kw = self._product_live_kwargs(handle.product_id)
            for key, val in live_kw.items():
                kwargs.setdefault(key, val)
            run(
                meeting_url=handle.meeting_url,
                graph_cfg=graph,
                product_id=handle.product_id,
                session_id=handle.session_id,
                page_id=flow[0],
                flow_id=flow[1],
                headful=self.headful,
                interactive_listen=False,
                open_meet_in_browser=open_browser,
                demo_origin=handle.origin,
                **kwargs,
                stop_event=handle._stop,
                on_bot_joined=on_bot_joined,
                on_meeting_ready=on_meeting_ready,
                on_leave_grace=on_leave_grace,
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
                print(f"[runner] live demo failed:\n{handle.error}", flush=True)
        finally:
            handle.finished_at = datetime.now(timezone.utc)
            handle.leave_grace_remaining = None
            with ActionLog(self.db_path) as log:
                entries = log.entries(handle.session_id, product_id=handle.product_id)
            handle.actions = len(entries)
            handle.failures = sum(
                1 for e in entries if not (e.verify and e.verify.passed)
            )
            self._finalize_live_demo(
                handle, operator_stopped=handle._stop.is_set()
            )
            self._persist_run(handle)
            self._store.save(handle)

    @staticmethod
    def _product_live_kwargs(product_id: str) -> dict:
        """Brain config + autonomy flags for live demo."""
        from navigator.agent.brain_config import BrainConfig
        from navigator.app.registry import ProductNotFound, Registry
        from navigator.core.settings import settings

        try:
            with Registry(settings.db_path) as reg:
                p = reg.get(product_id)
                cfg = BrainConfig.from_settings(
                    autonomy_mode=getattr(p, "autonomy_mode", None) or "guided",
                    tier2_legacy=bool(p.tier2_enabled),
                )
                return {
                    "tier2_enabled": cfg.tier2_enabled,
                    "brain_config": cfg,
                    "use_turn_brain": cfg.use_turn_brain,
                    "handoff_webhook_url": getattr(p, "handoff_webhook_url", "") or "",
                    "agent_settings": reg.get_agent_settings(product_id),
                }
        except Exception:  # noqa: BLE001
            cfg = BrainConfig.from_settings()
            return {
                "tier2_enabled": False,
                "brain_config": cfg,
                "use_turn_brain": cfg.use_turn_brain,
                "handoff_webhook_url": "",
            }

    @staticmethod
    def _product_tier2_enabled(product_id: str) -> bool:
        return bool(DemoRunner._product_live_kwargs(product_id).get("tier2_enabled"))
