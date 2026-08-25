"""Keep live-demo infra warm so "Show me the demo" starts fast.

A live demo cold-starts several slow things: the Attendee/Docker stack, a
Cloudflare quick tunnel for the ZAK/base URL, and a headed Chromium that has to
launch and navigate to the product before anything is on screen. This manager
keeps those warm in the background so the per-demo path skips the cold starts:

- **Attendee + Docker** kept up/healthy (``ensure_attendee_stack``).
- **Public base URL** (Cloudflare) pre-warmed and refreshed if it dies, so the
  Zoom host path does not tunnel on the click.
- **Parked Chromium** — one headed browser on the virtual display, sitting on
  the active product's landing page. After a demo it is navigated back to that
  page, so the product is always "on screen" ready to share.
- **Stage 2 warm session** — one pre-joined Meet (bot in meeting, waiting for
  human) for the primary product. ``/client/api/demos/start`` claims it so the
  join link returns instantly; a refill starts in the background.

Everything here is best-effort and self-healing: any failure logs and retries on
the next pass; it never raises into the app. Disabled in tests and when
``NAVIGATOR_WARM_POOL=0``.

ponytail: single parked browser + one warm Meet slot for the primary product.
Upgrade path: dict[product_id -> parked page / warm session] for multi-tenant.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from navigator.core.settings import settings

_PLACEHOLDER_HOSTS = ("example.com", "fixtures", "localhost", "127.0.0.1")
# How long to wait for Attendee "joined" before discarding a warm spawn.
# Must exceed wait_until_joined (180s) so Zoom ZAK can finish before we kill it.
_WARM_JOIN_TIMEOUT_S = 210.0


@dataclass
class _WarmSlot:
    product_id: str
    revision: int
    page_id: str
    flow_id: str
    origin: str
    handle: Any  # DemoHandle
    meeting: Any  # MeetingInfo
    ready_at: float = 0.0


class WarmPool:
    """Background keep-warm for attendee, Cloudflare, parked browser, warm Meet."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # Playwright objects live on the warm thread only (thread-affine).
        self._pw: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._parked_url: str = ""
        # Stage 2: pre-joined live sessions parked at "waiting for human".
        self._warm_sessions: dict[str, _WarmSlot] = {}
        self._warm_inflight: set[str] = set()
        self._warm_lock = threading.Lock()
        self.status: dict[str, Any] = {
            "attendee": False,
            "base_url": "",
            "browser": False,
            "parked_url": "",
            "warm_sessions": [],
            "last_pass": 0.0,
        }

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="warm-pool", daemon=True
        )
        self._thread.start()
        print("[warm] pool started", flush=True)

    def stop(self) -> None:
        self._stop.set()
        with self._warm_lock:
            for slot in list(self._warm_sessions.values()):
                try:
                    slot.handle._stop.set()
                except Exception:  # noqa: BLE001
                    pass
            self._warm_sessions.clear()
            self._warm_inflight.clear()
        self._publish_warm_status()
        with self._lock:
            self._teardown_browser()

    # -- product URL / id ----------------------------------------------------
    def _product_url(self) -> str:
        """Landing page of the primary registered product (skip placeholders)."""
        for candidate in (settings.product_url,):
            c = (candidate or "").strip()
            if c.startswith(("http://", "https://")) and not any(
                h in c.lower() for h in _PLACEHOLDER_HOSTS
            ):
                return c if c.endswith("/") else c + "/"
        try:
            from navigator.app.registry import Registry

            with Registry(settings.db_path) as reg:
                for product in reg.list_products():
                    try:
                        graph = reg.load_graph(product.product_id)
                    except Exception:  # noqa: BLE001
                        continue
                    base = (getattr(graph, "base_url", "") or "").strip()
                    if base.startswith(("http://", "https://")) and not any(
                        h in base.lower() for h in _PLACEHOLDER_HOSTS
                    ):
                        return base if base.endswith("/") else base + "/"
        except Exception as exc:  # noqa: BLE001
            print(f"[warm] product URL lookup failed: {exc}", flush=True)
        return ""

    def _primary_product_id(self) -> str:
        """First registered product with a real base_url (same filter as park).

        Uses the app Registry singleton (``NAVIGATOR_REGISTRY_DB``), not
        ``settings.db_path`` — those can differ and an empty id silently
        skips Stage 2 session warm while the parked browser still works via
        ``NAVIGATOR_PRODUCT_URL``.
        """
        try:
            from navigator.app.deps import get_registry

            registry = get_registry()
            products = list(registry.list_products())
            for product in products:
                try:
                    graph = registry.load_graph(product.product_id)
                except Exception:  # noqa: BLE001
                    continue
                base = (getattr(graph, "base_url", "") or "").strip()
                if base.startswith(("http://", "https://")) and not any(
                    h in base.lower() for h in _PLACEHOLDER_HOSTS
                ):
                    return product.product_id
            # Fallback: first product that loads any graph (park may use env URL).
            for product in products:
                try:
                    registry.load_graph(product.product_id)
                    return product.product_id
                except Exception:  # noqa: BLE001
                    continue
        except Exception as exc:  # noqa: BLE001
            print(f"[warm] primary product lookup failed: {exc}", flush=True)
        return ""

    # -- browser -------------------------------------------------------------
    def _teardown_browser(self) -> None:
        for closer in (
            lambda: self._page and self._page.context.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
        self._pw = self._browser = self._page = None
        self.status["browser"] = False

    def _browser_alive(self) -> bool:
        try:
            return bool(self._page and not self._page.is_closed())
        except Exception:  # noqa: BLE001
            return False

    def _ensure_browser(self, url: str) -> None:
        """Open (or reopen) the parked headed browser on ``url``."""
        if not url:
            return
        if self._browser_alive():
            if self._parked_url != url:
                self.park(url)
            return

        try:
            from navigator.automation.browser.cursor import install_cursor
            from navigator.automation.playwright_env import (
                ensure_headed_display,
                ensure_playwright_browsers,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[warm] browser deps import failed: {exc}", flush=True)
            return

        try:
            ensure_playwright_browsers()
            ensure_headed_display()
        except Exception as exc:  # noqa: BLE001
            print(f"[warm] no display for parked browser: {exc}", flush=True)
            return

        try:
            from playwright.sync_api import sync_playwright

            self._teardown_browser()
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=False)
            context = self._browser.new_context(
                viewport={"width": 1280, "height": 720}, device_scale_factor=1
            )
            self._page = context.new_page()
            try:
                install_cursor(self._page)
            except Exception:  # noqa: BLE001
                pass
            self._page.goto(url, wait_until="commit", timeout=30_000)
            self._parked_url = url
            self.status["browser"] = True
            self.status["parked_url"] = url
            print(f"[warm] parked Chromium on {url}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[warm] parked browser launch failed: {exc}", flush=True)
            self._teardown_browser()

    def park(self, url: str | None = None) -> None:
        """Navigate the parked browser back to the product landing page.

        Call after a demo ends so the product is always on screen. Safe to call
        from any thread — the actual nav happens best-effort here; if the browser
        is on another thread and rejects, the next warm pass repairs it.
        """
        target = (url or self._parked_url or self._product_url()).strip()
        if not target:
            return
        with self._lock:
            if not self._browser_alive():
                return
            try:
                self._page.goto(target, wait_until="commit", timeout=30_000)
                self._parked_url = target
                self.status["parked_url"] = target
                print(f"[warm] re-parked on {target}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[warm] re-park failed ({exc}); will relaunch", flush=True)
                self._teardown_browser()

    # -- Stage 2: pre-joined Meet --------------------------------------------
    def _slot_dead(self, slot: _WarmSlot) -> bool:
        h = slot.handle
        try:
            if h._stop.is_set():
                return True
            if h.status in ("failed", "finished"):
                return True
            if slot.ready_at and not h.bot_in_meeting:
                return True
        except Exception:  # noqa: BLE001
            return True
        return False

    def _publish_warm_status(self) -> None:
        with self._warm_lock:
            self.status["warm_sessions"] = [
                {
                    "product_id": s.product_id,
                    "revision": s.revision,
                    "page_id": s.page_id,
                    "flow_id": s.flow_id,
                    "ready": bool(s.ready_at),
                    "demo_id": str(getattr(s.handle, "demo_id", "")),
                    "bot_in_meeting": bool(
                        getattr(s.handle, "bot_in_meeting", False)
                    ),
                }
                for s in self._warm_sessions.values()
            ]

    def _latest_flow(
        self, product_id: str
    ) -> tuple[int, str, str, Any] | None:
        """(revision, page_id, flow_id, graph) for dashboard_test warm path."""
        try:
            from navigator.app.deps import get_registry

            registry = get_registry()
            revision = registry.latest_revision(product_id).revision
            graph = registry.load_graph(product_id, revision)
            primary = graph.primary_flow()
            if not primary:
                return None
            return revision, primary[0], primary[1], graph
        except Exception as exc:  # noqa: BLE001
            print(f"[warm] flow lookup failed for {product_id}: {exc}", flush=True)
            return None

    def _ensure_warm_session(self, product_id: str) -> None:
        if not product_id or self._stop.is_set():
            return
        flow = self._latest_flow(product_id)
        if flow is None:
            return
        revision, page_id, flow_id, _graph = flow

        with self._warm_lock:
            slot = self._warm_sessions.get(product_id)
            if slot is not None:
                if self._slot_dead(slot) or slot.revision != revision:
                    try:
                        slot.handle._stop.set()
                    except Exception:  # noqa: BLE001
                        pass
                    del self._warm_sessions[product_id]
                    print(
                        f"[warm] discarded stale/dead session for {product_id}",
                        flush=True,
                    )
                else:
                    self._publish_warm_status()
                    return
            if product_id in self._warm_inflight:
                return
            self._warm_inflight.add(product_id)

        threading.Thread(
            target=self._spawn_warm_session,
            args=(product_id, revision, page_id, flow_id),
            name=f"warm-session-{product_id}",
            daemon=True,
        ).start()

    def _create_open_meeting(self, product_id: str, *, topic: str):
        """Mint an open-access meeting for warm join; fall back Zoom if Meet SA dies."""
        from navigator.meeting.providers import MeetingProviderError, make_provider

        try:
            return make_provider(None).create_meeting(product_id, topic=topic)
        except MeetingProviderError as exc:
            primary = settings.meeting_platform
            if primary == "zoom":
                print(f"[warm] meeting create failed: {exc}", flush=True)
                return None
            # Meet SA / DWD often breaks; Zoom S2S still open-access for bot-first.
            try:
                print(
                    f"[warm] {primary} create failed ({exc}); trying zoom",
                    flush=True,
                )
                return make_provider("zoom").create_meeting(
                    product_id, topic=topic
                )
            except MeetingProviderError as zoom_exc:
                print(f"[warm] zoom create also failed: {zoom_exc}", flush=True)
                return None

    def _spawn_warm_session(
        self,
        product_id: str,
        revision: int,
        page_id: str,
        flow_id: str,
    ) -> None:
        """Create Meet + start_live; park when bot_in_meeting."""
        try:
            if self._stop.is_set():
                return
            from navigator.app.deps import get_registry, get_runner

            registry = get_registry()
            runner = get_runner()
            graph = registry.load_graph(product_id, revision)

            topic = f"Navigator demo — warm ({product_id})"
            meeting = self._create_open_meeting(product_id, topic=topic)
            if meeting is None:
                return
            if not meeting.open_access:
                print(
                    f"[warm] skip session: meeting not open-access ({meeting.url})",
                    flush=True,
                )
                return

            print(
                f"[warm] spawning session for {product_id} "
                f"rev={revision} {page_id}/{flow_id} via {meeting.platform}",
                flush=True,
            )
            handle = runner.start_live(
                product_id,
                graph,
                revision,
                (page_id, flow_id),
                meeting_url=meeting.url,
                platform=meeting.platform,
                origin="dashboard_test",
                auto_play=True,
            )

            deadline = time.time() + _WARM_JOIN_TIMEOUT_S
            while time.time() < deadline and not self._stop.is_set():
                if handle.status in ("failed", "finished") or handle._stop.is_set():
                    print(
                        f"[warm] session spawn failed "
                        f"status={handle.status} err={handle.error!r}",
                        flush=True,
                    )
                    return
                if handle.bot_in_meeting:
                    slot = _WarmSlot(
                        product_id=product_id,
                        revision=revision,
                        page_id=page_id,
                        flow_id=flow_id,
                        origin="dashboard_test",
                        handle=handle,
                        meeting=meeting,
                        ready_at=time.time(),
                    )
                    with self._warm_lock:
                        # Another slot raced in — stop the loser.
                        existing = self._warm_sessions.get(product_id)
                        if existing is not None and not self._slot_dead(existing):
                            handle._stop.set()
                            return
                        self._warm_sessions[product_id] = slot
                    self._publish_warm_status()
                    print(
                        f"[warm] session ready demo_id={handle.demo_id} "
                        f"url={meeting.url}",
                        flush=True,
                    )
                    return
                time.sleep(1.0)

            print(
                f"[warm] session join timeout for {product_id}; stopping",
                flush=True,
            )
            try:
                handle._stop.set()
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            print(f"[warm] session spawn error: {exc}", flush=True)
        finally:
            with self._warm_lock:
                self._warm_inflight.discard(product_id)
            self._publish_warm_status()

    def claim(
        self,
        product_id: str,
        *,
        revision: int,
        page_id: str,
        flow_id: str,
        origin: str,
        platform: str | None = None,
    ) -> Any | None:
        """Pop a ready warm session if it matches. Returns LiveDemoView or None."""
        if origin != "dashboard_test":
            return None
        with self._warm_lock:
            slot = self._warm_sessions.get(product_id)
            if slot is None or not slot.ready_at:
                return None
            if (
                slot.revision != revision
                or slot.page_id != page_id
                or slot.flow_id != flow_id
                or slot.origin != origin
            ):
                return None
            if platform is not None and getattr(slot.meeting, "platform", None) != platform:
                return None
            if self._slot_dead(slot) or not slot.handle.bot_in_meeting:
                del self._warm_sessions[product_id]
                return None
            del self._warm_sessions[product_id]
            handle = slot.handle
            meeting = slot.meeting

        self._publish_warm_status()
        print(
            f"[warm] claimed session demo_id={handle.demo_id} "
            f"product={product_id}",
            flush=True,
        )
        # Refill in background so the next click is warm again.
        threading.Thread(
            target=self._ensure_warm_session,
            args=(product_id,),
            name=f"warm-refill-{product_id}",
            daemon=True,
        ).start()

        from navigator.app.api_models import LiveDemoView, MeetingOut

        return LiveDemoView(
            **handle.public(), meeting=MeetingOut(**meeting.public())
        )

    # -- warm passes ---------------------------------------------------------
    def _warm_attendee(self) -> None:
        try:
            from navigator.meeting.attendee_stack import ensure_attendee_stack

            self.status["attendee"] = bool(ensure_attendee_stack())
        except Exception as exc:  # noqa: BLE001
            print(f"[warm] attendee keep-alive failed: {exc}", flush=True)
            self.status["attendee"] = False

    def _warm_base_url(self) -> None:
        try:
            from navigator.meeting.tunnel import tunnel_binary_available

            if not tunnel_binary_available(settings.tunnel_bin):
                return
            base = (settings.public_base_url or "").strip()
            if base and "trycloudflare.com" not in base:
                self.status["base_url"] = base
                return
            from navigator.meeting.zoom_host import ensure_public_base_url

            self.status["base_url"] = ensure_public_base_url()
        except Exception as exc:  # noqa: BLE001
            print(f"[warm] base URL keep-alive skipped: {exc}", flush=True)

    def _loop(self) -> None:
        interval = max(10.0, float(settings.warm_pool_interval_s))
        while not self._stop.is_set():
            self._warm_attendee()
            self._warm_base_url()
            with self._lock:
                try:
                    self._ensure_browser(self._product_url())
                except Exception as exc:  # noqa: BLE001
                    print(f"[warm] browser pass failed: {exc}", flush=True)
            # Stage 2: keep one pre-joined Meet ready for the primary product.
            try:
                pid = self._primary_product_id()
                if not pid:
                    print("[warm] no primary product for session warm", flush=True)
                elif not self.status.get("attendee"):
                    print("[warm] skip session: attendee not ready", flush=True)
                else:
                    self._ensure_warm_session(pid)
            except Exception as exc:  # noqa: BLE001
                print(f"[warm] session pass failed: {exc}", flush=True)
            self.status["last_pass"] = time.time()
            self._stop.wait(interval)


_pool: WarmPool | None = None
_pool_lock = threading.Lock()


def get_warm_pool() -> WarmPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = WarmPool()
        return _pool


def start_warm_pool() -> None:
    """Start the warm pool if enabled (no-op in tests / when disabled)."""
    if not settings.warm_pool:
        return
    if "pytest" in sys.modules:
        return
    get_warm_pool().start()


def park_browser_after_demo(url: str | None = None) -> None:
    """Return the parked browser to the product landing page after a demo."""
    if not settings.warm_pool:
        return
    try:
        get_warm_pool().park(url)
    except Exception as exc:  # noqa: BLE001
        print(f"[warm] park after demo failed: {exc}", flush=True)


def claim_warm_session(
    product_id: str,
    *,
    revision: int,
    page_id: str,
    flow_id: str,
    origin: str,
    platform: str | None = None,
) -> Any | None:
    """Claim a pre-joined warm Meet for dashboard_test, or None (cold start)."""
    if not settings.warm_pool:
        return None
    if "pytest" in sys.modules:
        return None
    try:
        return get_warm_pool().claim(
            product_id,
            revision=revision,
            page_id=page_id,
            flow_id=flow_id,
            origin=origin,
            platform=platform,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[warm] claim failed: {exc}", flush=True)
        return None
