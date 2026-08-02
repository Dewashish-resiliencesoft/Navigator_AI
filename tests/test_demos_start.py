"""POST /v1/demos/start: a meeting created per session, never an env var.

The runner is faked, so nothing here launches Attendee, cloudflared, or
Playwright. What is asserted is the contract between the three pieces: the route
creates a link, hands *that* link to the live runner, and returns it to the
caller -- and that a second tenant sees none of it.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from navigator.app import main as app_module
from navigator.app.registry import Registry
from navigator.app.runner import DemoRunner
from navigator.logs.store import ActionLog
from navigator.meeting.providers import MeetingInfo, MeetingProviderError
from test_api import ACME, GLOBEX, register


class FakeProvider:
    """Mints predictable links and records what it was asked for."""

    def __init__(self, platform="google_meet", fail: Exception | None = None) -> None:
        self.platform = platform
        self.fail = fail
        self.created: list[dict] = []
        self._n = 0

    def create_meeting(self, product_id, *, topic="") -> MeetingInfo:
        if self.fail:
            raise self.fail
        self._n += 1
        self.created.append({"product_id": product_id, "topic": topic})
        return MeetingInfo(
            url=f"https://meet.example/{product_id}-{self._n}",
            platform=self.platform,
            provider_id=f"spaces/{product_id}-{self._n}",
            open_access=True,
        )


class SpyRunner(DemoRunner):
    """A real runner whose live worker is replaced by a recorder."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.live_calls: list[dict] = []

    def start_live(self, product_id, graph, revision, flow, **kw):
        self.live_calls.append(
            {"product_id": product_id, "flow": flow, "graph": graph, **kw}
        )
        return super().start_live(
            product_id, graph, revision, flow, run=self._record, **kw
        )

    @staticmethod
    def _record(**kwargs) -> str:
        return "bot-1"


@pytest.fixture
def client(tmp_path):
    registry = Registry(tmp_path / "registry.db")
    log = ActionLog(tmp_path / "actions.db")
    runner = SpyRunner(
        str(tmp_path / "actions.db"), headful=False, archive_dir=tmp_path / "archives"
    )
    provider = FakeProvider()

    app_module.app.dependency_overrides[app_module.get_registry] = lambda: registry
    app_module.app.dependency_overrides[app_module.get_log] = lambda: log
    app_module.app.dependency_overrides[app_module.get_runner] = lambda: runner
    app_module.app.dependency_overrides[app_module.get_provider_factory] = (
        lambda: (lambda platform=None: provider)
    )

    with TestClient(app_module.app) as c:
        c.runner = runner
        c.provider = provider
        yield c

    app_module.app.dependency_overrides.clear()
    registry.close()
    log.close()


def start(client, product, **body):
    return client.post("/v1/demos/start", json=body, headers=product["headers"])


# -- the created link is the link that runs -----------------------------------


def test_start_creates_a_meeting_and_returns_the_link(client):
    p = register(client, "Acme Inbox", ACME)
    r = start(client, p, page_id="main", flow_id="happy_path")

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["meeting"]["url"] == "https://meet.example/acme-inbox-1"
    assert body["meeting"]["platform"] == "google_meet"
    assert body["meeting"]["open_access"] is True
    assert body["meeting_url"] == body["meeting"]["url"]
    assert client.provider.created == [
        {"product_id": "acme-inbox", "topic": "Navigator demo — Acme Inbox"}
    ]


def test_the_created_url_is_what_reaches_the_runner(client):
    p = register(client, "Acme Inbox", ACME)
    r = start(client, p, page_id="main", flow_id="happy_path")

    call = client.runner.live_calls[0]
    assert call["meeting_url"] == r.json()["meeting"]["url"]
    assert call["platform"] == "google_meet"
    assert call["product_id"] == "acme-inbox"
    assert call["flow"] == ("main", "happy_path")
    assert call["graph"].site == "acme-inbox", "the tenant's own graph, not a file"


def test_no_env_var_is_read(client, monkeypatch):
    """The point of the change: NAVIGATOR_MEETING_URL is dead on this path."""
    monkeypatch.setattr(app_module.settings, "meeting_url", "https://meet.google.com/STALE")
    p = register(client, "Acme Inbox", ACME)
    r = start(client, p, page_id="main", flow_id="happy_path")
    assert "STALE" not in r.text
    assert "STALE" not in client.runner.live_calls[0]["meeting_url"]


def test_non_open_access_meeting_is_rejected(tmp_path):
    """Public embed + static Meet = End User can't admit — refuse up front."""
    registry = Registry(tmp_path / "registry.db")
    log = ActionLog(tmp_path / "actions.db")
    runner = SpyRunner(
        str(tmp_path / "actions.db"), headful=False, archive_dir=tmp_path / "archives"
    )

    class ClosedProvider:
        platform = "static"

        def create_meeting(self, product_id, *, topic=""):
            return MeetingInfo(
                url="https://meet.google.com/abc-defg-hij",
                platform="static",
                provider_id="static",
                open_access=False,
            )

    provider = ClosedProvider()
    app_module.app.dependency_overrides[app_module.get_registry] = lambda: registry
    app_module.app.dependency_overrides[app_module.get_log] = lambda: log
    app_module.app.dependency_overrides[app_module.get_runner] = lambda: runner
    app_module.app.dependency_overrides[app_module.get_provider_factory] = (
        lambda: (lambda platform=None: provider)
    )
    client = TestClient(app_module.app)
    try:
        p = register(client, "Acme Inbox", ACME)
        r = client.post(
            "/v1/demos/start",
            headers=p["headers"],
            json={"platform": "static", "page_id": "main", "flow_id": "happy_path"},
        )
        assert r.status_code == 422, r.text
        assert "open-access" in r.text.lower() or "waiting" in r.text.lower()
        assert runner.live_calls == []
    finally:
        app_module.app.dependency_overrides.clear()
        registry.close()
        log.close()


def test_dashboard_static_uses_admit_flow(tmp_path):
    """Client test demo may use static Meet: Client is host and admits the bot."""
    from navigator.app.auth_store import AuthStore

    registry = Registry(tmp_path / "registry.db")
    log = ActionLog(tmp_path / "actions.db")
    auth_store = AuthStore(tmp_path / "auth.db")
    runner = SpyRunner(
        str(tmp_path / "actions.db"), headful=False, archive_dir=tmp_path / "archives"
    )

    class ClosedProvider:
        platform = "static"

        def create_meeting(self, product_id, *, topic=""):
            return MeetingInfo(
                url="https://meet.google.com/haw-cyyt-ynv",
                platform="static",
                provider_id="static",
                open_access=False,
            )

    provider = ClosedProvider()
    app_module.app.dependency_overrides[app_module.get_registry] = lambda: registry
    app_module.app.dependency_overrides[app_module.get_log] = lambda: log
    app_module.app.dependency_overrides[app_module.get_auth_store] = lambda: auth_store
    app_module.app.dependency_overrides[app_module.get_runner] = lambda: runner
    app_module.app.dependency_overrides[app_module.get_provider_factory] = (
        lambda: (lambda platform=None: provider)
    )
    client = TestClient(app_module.app)
    try:
        p = register(client, "Acme Inbox", ACME)
        auth_store.create_user(
            product_id=p["id"], email="client@acme.com", password="password"
        )
        login = client.post(
            "/v1/auth/login",
            json={"email": "client@acme.com", "password": "password"},
            headers={"Host": "localhost"},
        )
        assert login.status_code == 200, login.text
        headers = {
            "Host": "localhost",
            "Authorization": f"Bearer {login.json()['access_token']}",
        }
        r = client.post(
            "/client/api/demos/start",
            headers=headers,
            json={"platform": "static", "page_id": "main", "flow_id": "happy_path"},
        )
        assert r.status_code == 202, r.text
        assert r.json()["meeting"]["url"] == "https://meet.google.com/haw-cyyt-ynv"
        assert runner.live_calls, "runner should start"
        call = runner.live_calls[0]
        assert call.get("bot_first") is False
        assert call.get("open_meet_in_browser") is True
        assert call["origin"] == "dashboard_test"
    finally:
        app_module.app.dependency_overrides.clear()
        registry.close()
        log.close()


def test_two_sessions_get_two_meetings(client):
    p = register(client, "Acme Inbox", ACME)
    a = start(client, p, page_id="main", flow_id="happy_path").json()
    b = start(client, p, page_id="main", flow_id="happy_path").json()
    assert a["meeting"]["url"] != b["meeting"]["url"]
    assert a["demo_id"] != b["demo_id"]


def test_platform_choice_is_passed_to_the_factory(client):
    seen: list = []
    provider = FakeProvider(platform="zoom")
    app_module.app.dependency_overrides[app_module.get_provider_factory] = (
        lambda: (lambda platform=None: (seen.append(platform) or provider))
    )
    p = register(client, "Acme Inbox", ACME)
    r = start(client, p, page_id="main", flow_id="happy_path", platform="zoom")
    assert seen == ["zoom"]
    assert r.json()["meeting"]["platform"] == "zoom"


def test_unknown_platform_is_rejected_by_validation(client):
    p = register(client, "Acme Inbox", ACME)
    assert start(client, p, page_id="main", platform="webex").status_code == 422


# -- nothing is created for a bad request -------------------------------------


def test_no_graph_is_404_and_creates_no_meeting(client):
    p = register(client, "Acme Inbox")  # no upload
    assert start(client, p, page_id="main", flow_id="happy_path").status_code == 404
    assert client.provider.created == []


def test_unknown_flow_is_422_and_creates_no_meeting(client):
    p = register(client, "Acme Inbox", ACME)
    r = start(client, p, page_id="main", flow_id="nope")
    assert r.status_code == 422
    assert "no flow 'nope'" in r.json()["detail"]
    assert client.provider.created == [], "a bad request must not orphan a meeting"


def test_provider_failure_is_502_and_starts_no_demo(client):
    app_module.app.dependency_overrides[app_module.get_provider_factory] = (
        lambda: (lambda platform=None: FakeProvider(
            fail=MeetingProviderError("HTTP 403: Meet API disabled")
        ))
    )
    p = register(client, "Acme Inbox", ACME)
    r = start(client, p, page_id="main", flow_id="happy_path")
    assert r.status_code == 502
    assert "Meet API disabled" in r.json()["detail"]
    assert client.runner.live_calls == []


def test_start_requires_a_key(client):
    assert client.post("/v1/demos/start", json={"page_id": "main"}).status_code == 401


# -- defaults -----------------------------------------------------------------


def test_flow_defaults_to_the_configured_walkthrough(client, monkeypatch):
    monkeypatch.setattr(app_module.settings, "live_walkthrough_flow", "happy_path")
    p = register(client, "Acme Inbox", ACME)
    assert start(client, p).status_code == 202
    assert client.runner.live_calls[0]["flow"] == ("main", "happy_path")


def test_intake_prefill_reaches_the_runner(client):
    p = register(client, "Acme Inbox", ACME)
    start(
        client,
        p,
        page_id="main",
        flow_id="happy_path",
        intake={"name": "Dana", "company": "Acme"},
    )
    prefill = client.runner.live_calls[0]["intake_prefill"]
    assert prefill["name"] == "Dana"
    assert prefill["company"] == "Acme"


def test_no_intake_means_no_prefill(client):
    p = register(client, "Acme Inbox", ACME)
    start(client, p, page_id="main", flow_id="happy_path")
    assert client.runner.live_calls[0]["intake_prefill"] is None


# -- tenant isolation ---------------------------------------------------------


def test_one_tenants_live_demo_is_invisible_to_another(client):
    acme = register(client, "Acme Inbox", ACME)
    globex = register(client, "Globex Desk", GLOBEX)

    a = start(client, acme, page_id="main", flow_id="happy_path").json()
    client.runner.wait(uuid.UUID(a["demo_id"]), timeout=10)

    assert client.get(f"/v1/demos/{a['demo_id']}", headers=globex["headers"]).status_code == 404
    assert (
        client.get(f"/v1/demos/{a['demo_id']}/actions", headers=globex["headers"]).status_code
        == 404
    )
    assert client.get("/v1/demos", headers=globex["headers"]).json() == []
    mine = client.get("/v1/demos", headers=acme["headers"]).json()
    assert [d["demo_id"] for d in mine] == [a["demo_id"]]


def test_each_tenant_gets_its_own_meeting_and_graph(client):
    acme = register(client, "Acme Inbox", ACME)
    globex = register(client, "Globex Desk", GLOBEX)

    a = start(client, acme, page_id="main", flow_id="happy_path").json()
    g = start(client, globex, page_id="main", flow_id="happy_path").json()

    assert a["meeting"]["url"] != g["meeting"]["url"]
    assert "acme-inbox" in a["meeting"]["url"]
    assert "globex-desk" in g["meeting"]["url"]
    sites = [c["graph"].site for c in client.runner.live_calls]
    assert sites == ["acme-inbox", "globex-desk"]


def test_the_link_survives_on_the_demo_view(client):
    p = register(client, "Acme Inbox", ACME)
    started = start(client, p, page_id="main", flow_id="happy_path").json()
    client.runner.wait(uuid.UUID(started["demo_id"]), timeout=10)
    view = client.get(f"/v1/demos/{started['demo_id']}", headers=p["headers"]).json()
    assert view["meeting_url"] == started["meeting"]["url"]
    assert view["platform"] == "google_meet"


def test_the_runner_passes_the_link_into_run_live_meet_demo(tmp_path):
    """One level down: DemoRunner.start_live -> run_live_meet_demo(meeting_url=...)."""
    seen: dict = {}

    def fake_run(**kwargs) -> str:
        seen.update(kwargs)
        return "bot-9"

    runner = DemoRunner(str(tmp_path / "a.db"), headful=False)
    handle = runner.start_live(
        "acme-inbox",
        object(),
        1,
        ("main", "happy_path"),
        meeting_url="https://meet.example/fresh",
        platform="google_meet",
        origin="public_embed",
        run=fake_run,
    )
    runner.wait(handle.demo_id, timeout=10)

    assert handle.status == "finished", handle.error
    assert seen["meeting_url"] == "https://meet.example/fresh"
    assert seen["product_id"] == "acme-inbox"
    assert seen["session_id"] == handle.session_id
    assert seen["page_id"] == "main" and seen["flow_id"] == "happy_path"
    assert seen["interactive_listen"] is False, "an API-started demo has no TTY"
    assert seen["open_meet_in_browser"] is False, "no browser on the API host"


def test_a_live_run_that_raises_is_recorded_not_swallowed(tmp_path):
    def boom(**_kw):
        raise RuntimeError("attendee refused")

    runner = DemoRunner(str(tmp_path / "a.db"), headful=False)
    handle = runner.start_live(
        "acme-inbox",
        object(),
        1,
        ("main", "happy_path"),
        meeting_url="https://meet.example/x",
        platform="google_meet",
        origin="public_embed",
        run=boom,
    )
    runner.wait(handle.demo_id, timeout=10)
    assert handle.status == "failed"
    assert "attendee refused" in (handle.error or "")
