"""The wrapper API: one deployment, many products.

A customer registers a product, uploads a site graph, and asks for a demo. The
site graph is the only interface between Navigator and any product, so this layer
adds no product-specific logic whatsoever -- it is a registry, an authenticator,
and a way to start demos.

Run it:
    .venv/bin/uvicorn navigator.app.main:app --reload --workers 1

`--workers 1` matters: live demo state is in-process. See DemoRunner.
"""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Callable
from uuid import UUID
from datetime import datetime, timezone

from fastapi import (
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from navigator.client.auth import persist_client_key, resolve_client_api_key
from navigator.client.dashboard import (
    WEB_ASSETS,
    client_index_html,
    require_local_ops,
)
from navigator.client.content import (
    apply_playlist_to_yaml,
    reset_site_graph_for_explore,
    begin_capture,
    merge_recorded_flow,
    playlist_from_graph,
    recorder_status,
    recording_base_url,
    remove_flow_from_yaml,
    resolve_flow_page_id,
    start_recorder,
    stop_recorder,
)
from navigator.knowledge.company_bio import load_bio, save_bio
from navigator.knowledge.product_brief import load_product_brief, save_product_brief
from navigator.knowledge.demo_script import (
    apply_script_patch,
    compose_full_demo_script,
    merge_manual_overrides,
    regenerate_demo_script,
)
from navigator.app.registry import (
    NewProduct,
    Product,
    ProductNotFound,
    Registry,
    RegistryError,
    RegisteredProduct,
    SiteGraphRevision,
    SiteGraphSource,
)
from navigator.app.credential_vault import (
    CredentialVault,
    CredentialVaultError,
    VaultNotConfigured,
)
from navigator.app.runner import DemoOrigin, DemoRunner

import jwt

from navigator.auth import AuthStore, AuthError, SessionTokenStore, SessionTokenError
from navigator.auth.routes import build_auth_router
from navigator.app.auth_store import InvalidCredentials
from navigator.knowledge.site_graph import SiteGraphError, parse_site_graph
from navigator.meeting.providers import (
    MeetingProvider,
    MeetingProviderError,
    Platform as MeetingPlatform,
    ZoomProvider,
    make_provider,
)
from navigator.logs.store import ActionLog
from navigator.core.schemas import ActionLogEntry
from navigator.core.settings import settings


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    from navigator.meeting.attendee_stack import ensure_attendee_stack

    ensure_attendee_stack()
    yield


app = FastAPI(
    title="Navigator AI",
    version="0.1.0",
    description="Live interactive demo agent for any web product.",
    lifespan=_lifespan,
)


# -- wiring -------------------------------------------------------------------
# Module-level singletons, overridable in tests via dependency_overrides.


def get_registry() -> Registry:
    return _registry


def get_runner() -> DemoRunner:
    return _runner


def get_log() -> ActionLog:
    return _log


def get_provider_factory() -> Callable[[MeetingPlatform | None], MeetingProvider]:
    """A *factory*, not a provider: the platform is chosen per request.

    Tests override this to hand back a fake, so no test ever calls Google or Zoom.
    """
    return make_provider


_registry = Registry(os.environ.get("NAVIGATOR_REGISTRY_DB", "registry.db"))
_log = ActionLog(settings.db_path)

_token_store = SessionTokenStore(settings.db_path)

def get_token_store() -> SessionTokenStore:
    return _token_store

_auth_store = AuthStore(settings.db_path)

def get_auth_store() -> AuthStore:
    return _auth_store


app.include_router(
    build_auth_router(get_registry=get_registry, get_auth_store=get_auth_store)
)

_runner = DemoRunner(str(settings.db_path), headful=settings.headful)

_vault = CredentialVault(settings.credential_db_path)

def get_vault() -> CredentialVault:
    return _vault


def authed(
    registry: Annotated[Registry, Depends(get_registry)],
    authorization: Annotated[str | None, Header()] = None,
) -> Product:
    """Resolve the caller's product from its API key.

    Every product-scoped route depends on this, so a route cannot accidentally
    read across tenants: the product_id comes from the key, never from the path.
    """
    if not authorization or not authorization.lower().startswith("token "):
        raise HTTPException(401, "expected: Authorization: Token <api key>")
    try:
        return registry.authenticate(authorization.split(None, 1)[1].strip())
    except ProductNotFound:
        raise HTTPException(401, "invalid API key") from None


AuthedProduct = Annotated[Product, Depends(authed)]

def dashboard_authed(
    registry: Annotated[Registry, Depends(get_registry)],
    authorization: Annotated[str | None, Header()] = None,
) -> Product:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "expected: Authorization: Bearer <jwt>")
    
    token = authorization.split(None, 1)[1].strip()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        product_id = payload.get("product_id")
        if not product_id:
            raise HTTPException(401, "invalid JWT payload")
        return registry.get(product_id)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid token") from None
    except ProductNotFound:
        raise HTTPException(401, "product not found") from None

DashboardAuthedProduct = Annotated[Product, Depends(dashboard_authed)]

class IntakePrefill(BaseModel):
    """What the landing page already knows, so the bot needn't ask again."""
    name: str = ""
    company: str = ""
    business_type: str = ""
    looking_for: str = ""

def authed_or_session(
    registry: Annotated[Registry, Depends(get_registry)],
    store: Annotated[SessionTokenStore, Depends(get_token_store)],
    authorization: Annotated[str | None, Header()] = None,
) -> tuple[Product, IntakePrefill | None]:
    """Auth for the live-demo path: the Client's server key, or an embed token.

    Both credentials mean a LIVE demo. A dashboard JWT is deliberately not
    accepted here -- a Client's own test run must go through the dashboard route
    so it is never counted as live traffic. See docs/PRODUCT_MODEL.md.
    """
    if not authorization or not authorization.lower().startswith("token "):
        raise HTTPException(401, "expected: Authorization: Token <key_or_token>")

    token = authorization.split(None, 1)[1].strip()
    if token.startswith("nav_"):
        try:
            return registry.authenticate(token), None
        except ProductNotFound:
            raise HTTPException(401, "invalid API key") from None
    elif token.startswith("sess_"):
        try:
            result = store.consume_token(token)
            product = registry.get(result["product_id"])
            intake = IntakePrefill(**result["intake"]) if result["intake"] else None
            return product, intake
        except SessionTokenError as exc:
            raise HTTPException(401, str(exc)) from None
        except ProductNotFound:
            raise HTTPException(401, "product not found") from None
    else:
        raise HTTPException(401, "invalid token format")

AuthedOrSession = Annotated[tuple[Product, IntakePrefill | None], Depends(authed_or_session)]
Reg = Annotated[Registry, Depends(get_registry)]
Vault = Annotated[CredentialVault, Depends(get_vault)]
Runner = Annotated[DemoRunner, Depends(get_runner)]
Log = Annotated[ActionLog, Depends(get_log)]
Providers = Annotated[
    Callable[[MeetingPlatform | None], MeetingProvider],
    Depends(get_provider_factory),
]


def _reject_login_in_yaml(
    product_id: str,
    yaml_text: str,
    vault: CredentialVault,
    *,
    allow_flows: frozenset[tuple[str, str]] | None = None,
) -> None:
    """Save/activate gate: login steps belong in Product Login, not in flows."""
    from navigator.automation.login_match import LoginConfig, assert_no_login_in_graph

    graph = parse_site_graph(yaml_text, origin=f"product {product_id}")
    assert_no_login_in_graph(
        graph,
        LoginConfig(login_url=vault.login_url(product_id)),
        allow_flows=allow_flows,
    )


# -- request/response bodies --------------------------------------------------


class SiteGraphUpload(BaseModel):
    yaml: str = Field(min_length=1)
    source: SiteGraphSource = "yaml"
    publish: bool = False
    """Default false: an upload is a draft until the Client publishes it."""


class NewDemo(BaseModel):
    page_id: str
    flow_id: str
    meeting_url: str | None = None
    """Ignored here -- POST /v1/demos runs headless, no meeting. See
    POST /v1/demos/start, which creates its own link."""


class DemoView(BaseModel):
    demo_id: UUID
    product_id: str
    revision: int
    session_id: UUID
    origin: DemoOrigin
    status: str
    page_id: str
    actions: int
    failures: int
    error: str | None = None
    said: list[str] = Field(default_factory=list)
    meeting_url: str | None = None
    platform: str | None = None
    bot_in_meeting: bool = False


class DemoRunView(BaseModel):
    """Persisted demo run meta for the client Logs panel (7-day window)."""

    session_id: UUID
    demo_id: UUID
    product_id: str
    platform: str
    status: str
    origin: DemoOrigin = "dashboard_test"
    host_os: str = ""
    host_release: str = ""
    host_machine: str = ""
    host_name: str = ""
    browser: str = ""
    meeting_label: str = ""
    started_at: datetime
    ended_at: datetime | None = None
    fail_count: int = 0


class SessionTokenRequest(BaseModel):
    intake: IntakePrefill | None = None
    expires_in_seconds: int = Field(default=3600, ge=60, le=86400)

class SessionTokenResponse(BaseModel):
    token: str
    expires_at: str
    product_id: str


class StartLiveDemo(BaseModel):
    platform: MeetingPlatform | None = None
    """None -> NAVIGATOR_MEETING_PLATFORM."""
    topic: str | None = None
    page_id: str | None = None
    flow_id: str | None = None
    """None -> NAVIGATOR_LIVE_WALKTHROUGH_FLOW."""
    intake: IntakePrefill | None = None
    auto_play: bool = True
    """When True, finish one playlist flow then continue to the next."""


class MeetingOut(BaseModel):
    url: str
    platform: str
    provider_id: str
    passcode: str = ""
    open_access: bool = False
    """True when the link admits anyone directly -- Navigator can join first
    with nobody to let it out of the waiting room."""


class LiveDemoView(DemoView):
    meeting: MeetingOut


# -- products -----------------------------------------------------------------


@app.post("/v1/products", response_model=RegisteredProduct, status_code=201)
def register_product(spec: NewProduct, registry: Reg) -> RegisteredProduct:
    """Register a product. The API key in the response is shown exactly once."""
    try:
        return registry.register(spec)
    except RegistryError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/v1/products/me", response_model=Product)
def whoami(product: AuthedProduct) -> Product:
    return product

class SystemMetrics(BaseModel):
    host_label: str
    uptime_s: float
    cpu_percent: float
    cpu_count: int
    memory_percent: float
    memory_used_mb: float
    memory_total_mb: float
    net_sent_bytes: int
    net_recv_bytes: int
    gpu: dict[str, Any]
    services: list[dict[str, str]]
    processes: list[dict[str, str]]
    health: list[dict[str, Any]]
    token_usage: dict[str, Any] | None = None


@app.get("/client/api/system/health", response_model=SystemMetrics)
def client_system_health(
    product: DashboardAuthedProduct,
    registry: Reg,
    runner: Runner,
    vault: Vault,
) -> SystemMetrics:
    """Real host metrics + Navigator service status for the Client dashboard."""
    from navigator.app.system_health import collect_system_health

    payload = collect_system_health(
        product_id=product.product_id,
        registry=registry,
        runner=runner,
        db_path=str(settings.db_path),
        vault=vault,
    )
    return SystemMetrics(**payload)

# -- site graph ---------------------------------------------------------------


@app.put("/v1/products/site-graph", response_model=SiteGraphRevision, status_code=201)
def upload_site_graph(
    upload: SiteGraphUpload, product: AuthedProduct, registry: Reg, vault: Vault
) -> SiteGraphRevision:
    """Validate and store a new site graph revision as a draft.

    An upload does not go live on its own: pass `publish: true`, or call
    POST /v1/products/site-graph/activate afterwards. Pushing a graph must not
    change what End Users see mid-edit.

    Validation runs before anything is written, so a rejected upload cannot break
    a live demo. The error text is identical to what the file loader produces --
    there is one validator in the system, deliberately.
    """
    try:
        _reject_login_in_yaml(product.product_id, upload.yaml, vault)
        return registry.put_site_graph(
            product.product_id, upload.yaml, upload.source, publish=upload.publish
        )
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/v1/products/site-graph", response_model=SiteGraphRevision)
def get_site_graph(
    product: AuthedProduct,
    registry: Reg,
    revision: Annotated[int | None, Query()] = None,
) -> SiteGraphRevision:
    try:
        return registry.get_revision(product.product_id, revision)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None


@app.get("/v1/products/site-graph/revisions", response_model=list[SiteGraphRevision])
def list_revisions(product: AuthedProduct, registry: Reg) -> list[SiteGraphRevision]:
    return registry.revisions(product.product_id)


@app.post("/v1/products/site-graph/activate", response_model=Product)
def activate_revision(
    product: AuthedProduct,
    registry: Reg,
    vault: Vault,
    revision: Annotated[int, Body(embed=True)],
) -> Product:
    """Roll back to an earlier revision."""
    try:
        rev = registry.get_revision(product.product_id, revision)
        _reject_login_in_yaml(product.product_id, rev.yaml, vault)
        return registry.activate(product.product_id, revision)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/v1/products/flows")
def list_flows(product: AuthedProduct, registry: Reg) -> dict[str, list[str]]:
    """Which flows this product's active site graph offers, per page."""
    try:
        graph = registry.load_graph(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return {pid: sorted(page.flows) for pid, page in graph.pages.items()}


# -- demos --------------------------------------------------------------------


@app.post("/v1/demos", response_model=DemoView, status_code=202)
def start_demo(
    spec: NewDemo, product: AuthedProduct, registry: Reg, runner: Runner
) -> DemoView:
    """Start a headless demo. Returns immediately; poll GET /v1/demos/{id}.

    No meeting and no End User: this is a Client verifying a flow (what
    `navigator verify` drives), so it is a test demo and runs their latest
    revision, draft included.
    """
    try:
        revision = registry.latest_revision(product.product_id).revision
        graph = registry.load_graph(product.product_id, revision)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None

    try:
        graph.flow(spec.page_id, spec.flow_id)  # fail fast on a bad flow
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None

    handle = runner.start(
        product.product_id,
        graph,
        revision,
        (spec.page_id, spec.flow_id),
        origin="dashboard_test",
    )
    return DemoView(**handle.public())



@app.post("/v1/session-tokens", response_model=SessionTokenResponse, status_code=201)
def create_session_token(
    req: SessionTokenRequest,
    product: AuthedProduct,
    store: Annotated[SessionTokenStore, Depends(get_token_store)]
) -> dict:
    try:
        intake_dict = req.intake.model_dump() if req.intake else None
        token, expires_at = store.create_token(
            product.product_id, 
            intake_dict, 
            req.expires_in_seconds
        )
        return {
            "token": token,
            "expires_at": expires_at.isoformat(),
            "product_id": product.product_id
        }
    except SessionTokenError as exc:
        raise HTTPException(429, str(exc)) from None

@app.post("/v1/demos/start", response_model=LiveDemoView, status_code=202)
def start_live_demo(
    spec: StartLiveDemo,
    auth_ctx: AuthedOrSession,
    registry: Reg,
    runner: Runner,
    providers: Providers,
) -> LiveDemoView:
    """A LIVE demo: an End User on the Client's landing page. Billable.

    Runs the Client's published revision only. See _run_live_demo.
    """
    product, token_intake = auth_ctx
    return _run_live_demo(
        spec,
        product,
        token_intake,
        registry,
        runner,
        providers,
        origin="public_embed",
    )


def _run_live_demo(
    spec: StartLiveDemo,
    product: Product,
    token_intake: IntakePrefill | None,
    registry: Registry,
    runner: DemoRunner,
    providers: Callable[[MeetingPlatform | None], MeetingProvider],
    *,
    origin: DemoOrigin,
) -> LiveDemoView:
    """Create a meeting for *this* session and put Navigator in it, now.

    The meeting is instant, not scheduled: it exists and is joinable the moment
    this returns, which is the only useful semantics for a "Show Demo" button.
    The link is minted per call, so two prospects can be demoed at once and no
    human has to set NAVIGATOR_MEETING_URL first. The response carries the join
    URL, which is what the button redirects to.

    Ordering is deliberate: the graph is loaded and the flow validated *before*
    any meeting is created, so a bad request never leaves an orphaned meeting
    behind.

    Revision resolution is the boundary that matters here. A live demo runs the
    published revision and nothing else, so an End User can never be shown a
    half-finished draft the Client is still editing. A dashboard test demo runs
    the Client's latest revision, draft included -- validating a draft before
    publishing is the entire point of a test demo.
    """
    try:
        if origin == "public_embed":
            revision = registry.published_revision(product.product_id)
        else:
            revision = registry.latest_revision(product.product_id).revision
        graph = registry.load_graph(product.product_id, revision)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None

    autonomy_mode = getattr(product, "autonomy_mode", None) or "guided"
    if origin == "public_embed" and autonomy_mode == "explorer":
        raise HTTPException(
            400,
            "Explorer mode is only for dashboard test demos, not the public embed.",
        )

    try:
        rev_yaml = registry.get_revision(product.product_id, revision).yaml
        from navigator.agent.readiness import assert_live_graph_yaml, assess_demo_readiness

        assert_live_graph_yaml(rev_yaml)
        readiness = assess_demo_readiness(
            registry,
            product.product_id,
            origin=origin,
            autonomy_mode=autonomy_mode,
        )
        blocking = [c for c in readiness.checks if c.blocking and not c.ok]
        if blocking:
            raise HTTPException(422, f"Demo not ready: {blocking[0].message}")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    page_id = spec.page_id
    flow_id = spec.flow_id
    if spec.auto_play and graph.demo_playlist:
        primary = graph.primary_flow()
        if primary:
            page_id, flow_id = primary
    elif not page_id or not flow_id:
        primary = graph.primary_flow()
        if primary:
            page_id = page_id or primary[0]
            flow_id = flow_id or primary[1]
    page_id = page_id or next(iter(graph.pages), "")
    if not flow_id:
        flow_id = settings.live_walkthrough_flow
    try:
        graph.flow(page_id, flow_id)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None

    try:
        topic = spec.topic or f"Navigator demo — {product.name}"
        meeting = providers(spec.platform).create_meeting(
            product.product_id, topic=topic
        )
    except MeetingProviderError as exc:
        # 502: the request was fine, the upstream conferencing provider was not.
        raise HTTPException(502, f"could not create meeting: {exc}") from None

    if not meeting.open_access:
        if origin == "public_embed":
            # End Users cannot admit a bot from a waiting room.
            raise HTTPException(
                422,
                "This meeting link is not open-access: Navigator would wait in the "
                "lobby for a host that never comes. Use platform 'google_meet' "
                "(creates a new open Meet space) or 'zoom' (Navigator joins as "
                "Zoom host via ZAK).",
            )
        # Dashboard test + static: Client is the host. Navigator joins as guest;
        # Client opens the link and admits the bot (admit-flow, not bot-first).
        print(
            f"[api] dashboard_test static link {meeting.url} — "
            "admit-flow (you are host; admit Navigator when Meet asks)",
            flush=True,
        )

    live_kw: dict = {
        "intake_prefill": (
            (token_intake or spec.intake).model_dump()
            if (token_intake or spec.intake)
            else None
        ),
        "auto_play": bool(spec.auto_play),
    }
    if not meeting.open_access and origin == "dashboard_test":
        live_kw["bot_first"] = False
        live_kw["open_meet_in_browser"] = True

    handle = runner.start_live(
        product.product_id,
        graph,
        revision,
        (page_id, flow_id),
        meeting_url=meeting.url,
        platform=meeting.platform,
        origin=origin,
        **live_kw,
    )
    return LiveDemoView(**handle.public(), meeting=MeetingOut(**meeting.public()))


@app.get("/v1/demos/{demo_id}", response_model=DemoView)
def get_demo(demo_id: UUID, product: AuthedProduct, runner: Runner) -> DemoView:
    handle = runner.get(demo_id, product.product_id)
    if handle is None:
        raise HTTPException(404, "no such demo")
    return DemoView(**handle.public())


@app.get("/v1/demos", response_model=list[DemoView])
def list_demos(product: AuthedProduct, runner: Runner) -> list[DemoView]:
    return [DemoView(**h.public()) for h in runner.list(product.product_id)]


@app.post("/v1/demos/{demo_id}/end", response_model=DemoView)
def end_demo(
    demo_id: UUID, product: AuthedProduct, runner: Runner, log: Log
) -> DemoView:
    handle = runner.stop(demo_id, product.product_id)
    if handle is not None:
        return DemoView(**handle.public())

    # Stale UI / post-restart: demo_runs still says running but worker is gone.
    row = log.get_run_by_demo_id(demo_id, product.product_id)
    if row is None:
        raise HTTPException(404, "no such demo")
    from navigator.logs.store import utcnow

    log.update_run_status(
        UUID(row["session_id"]), "finished", ended_at=utcnow()
    )
    return DemoView(
        demo_id=demo_id,
        product_id=product.product_id,
        revision=0,
        session_id=UUID(row["session_id"]),
        origin=row["origin"],
        status="finished",
        page_id="",
        actions=0,
        failures=int(row.get("fail_count") or 0),
        error=None,
        said=[],
        meeting_url=None,
        platform=row.get("platform"),
        bot_in_meeting=False,
    )


@app.get("/v1/demos/{demo_id}/actions", response_model=list[ActionLogEntry])
def demo_actions(
    demo_id: UUID, product: AuthedProduct, runner: Runner, log: Log
) -> list[ActionLogEntry]:
    """The ActionLog for one demo: every call, its expectation, and what happened.

    This is the audit trail that makes the product sellable -- a customer can see
    exactly what the agent did in front of their prospect.
    """
    handle = runner.get(demo_id, product.product_id)
    if handle is None:
        raise HTTPException(404, "no such demo")
    return log.entries(handle.session_id, product_id=product.product_id)


# -- health / failures --------------------------------------------------------


@app.get("/v1/products/failures", response_model=list[ActionLogEntry])
def product_failures(
    product: AuthedProduct,
    log: Log,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[ActionLogEntry]:
    """Recent failures across all demos of this product -- which flows are rotting."""
    return log.product_failures(product.product_id, limit)


@app.get("/v1/products/corrections/pending")
def pending_corrections(product: AuthedProduct) -> list[dict]:
    """Reflection output awaiting human approval.

    Never auto-promoted: an agent that can silently rewrite its own rules is
    not debuggable.
    """
    from navigator.knowledge.memory.pending import PendingCorrectionStore

    with PendingCorrectionStore(settings.db_path) as store:
        return [row.as_dict() for row in store.list_pending(product.product_id)]


class ApproveCorrectionBody(BaseModel):
    """Optional override when promoting a pending rule into Chroma."""

    rule: str | None = None


@app.post("/v1/products/corrections/{correction_id}/approve")
def approve_correction(
    correction_id: str,
    product: AuthedProduct,
    body: ApproveCorrectionBody | None = None,
) -> dict:
    """Human approves a pending rule → live Chroma corrections collection."""
    from navigator.knowledge.memory.pending import PendingCorrectionStore
    from navigator.knowledge.memory.seed import seed_correction

    body = body or ApproveCorrectionBody()
    with PendingCorrectionStore(settings.db_path) as store:
        row = store.get(correction_id, product.product_id)
        if row is None:
            raise HTTPException(404, "no such pending correction")
        if row.status != "pending":
            raise HTTPException(409, f"correction already {row.status}")
        rule = (body.rule or row.rule).strip()
        doc_id = seed_correction(
            settings.chroma_path,
            product_id=product.product_id,
            rule=rule,
            page=row.page,
            tool_call_type=row.tool_call_type,
            source_call_id=row.source_call_id,
            doc_id=row.id,
        )
        updated = store.set_status(correction_id, product.product_id, "approved")
    return {
        "id": correction_id,
        "status": "approved",
        "chroma_id": doc_id,
        "rule": rule,
        "product_id": product.product_id,
        "row": None if updated is None else updated.as_dict(),
    }


@app.post("/v1/products/corrections/{correction_id}/reject")
def reject_correction(correction_id: str, product: AuthedProduct) -> dict:
    from navigator.knowledge.memory.pending import PendingCorrectionStore

    with PendingCorrectionStore(settings.db_path) as store:
        row = store.get(correction_id, product.product_id)
        if row is None:
            raise HTTPException(404, "no such pending correction")
        if row.status != "pending":
            raise HTTPException(409, f"correction already {row.status}")
        updated = store.set_status(correction_id, product.product_id, "rejected")
    return {
        "id": correction_id,
        "status": "rejected",
        "row": None if updated is None else updated.as_dict(),
    }


class KnowledgeIngestBody(BaseModel):
    text: str = Field(min_length=1)


@app.post("/v1/products/knowledge", status_code=201)
def ingest_knowledge(body: KnowledgeIngestBody, product: AuthedProduct, registry: Reg) -> dict:
    """Add product knowledge: chunk, tag, and deduplicate."""
    from navigator.knowledge.ingest import ingest_knowledge_text

    try:
        current_revision = registry.published_revision(product.product_id)
    except ProductNotFound:
        current_revision = None

    chunk_ids = ingest_knowledge_text(
        body.text,
        product.product_id,
        revision_tied_to=current_revision,
        judge=None,  # v1: no LLM tagging; placeholder for future
        chroma_path=settings.chroma_path,
    )
    return {
        "chunk_ids": chunk_ids,
        "chunk_count": len(chunk_ids),
        "product_id": product.product_id,
        "ingested_at_revision": current_revision,
    }


@app.get("/")
def root() -> RedirectResponse:
    """Landing URL when the API starts — Client dashboard, not OpenAPI docs."""
    return RedirectResponse(url="/client", status_code=307)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


# -- client dashboard (tenant companies) ---------------------------------------------------


@app.get("/client", response_class=HTMLResponse)
def client_console(request: Request) -> HTMLResponse:
    require_local_ops(request)
    return HTMLResponse(client_index_html())


if WEB_ASSETS.is_dir():
    # Vite emits content-hashed filenames, so these are safe to cache hard.
    app.mount("/client/assets", StaticFiles(directory=WEB_ASSETS), name="client-assets")


@app.get("/ops")
def legacy_ops_redirect() -> RedirectResponse:
    return RedirectResponse(url="/client", status_code=307)


@app.post("/client/api/demos/start", response_model=LiveDemoView, status_code=202)
def client_start_live_demo(
    product: DashboardAuthedProduct,
    spec: StartLiveDemo,
    registry: Reg,
    runner: Runner,
    providers: Providers,
) -> LiveDemoView:
    """A TEST demo: the Client checking their own setup. Not billable.

    JWT-only, so an End User cannot reach it, and it runs the Client's latest
    revision (draft included) rather than the published one.
    """
    return _run_live_demo(
        spec, product, None, registry, runner, providers, origin="dashboard_test"
    )


@app.get("/client/api/demos", response_model=list[DemoView])
def client_list_demos(product: DashboardAuthedProduct, runner: Runner) -> list[DemoView]:
    return list_demos(product, runner)


@app.get("/client/api/demos/{demo_id}", response_model=DemoView)
def client_get_demo(demo_id: UUID, product: DashboardAuthedProduct, runner: Runner) -> DemoView:
    return get_demo(demo_id, product, runner)


@app.post("/client/api/demos/{demo_id}/end", response_model=DemoView)
def client_end_demo(
    demo_id: UUID, product: DashboardAuthedProduct, runner: Runner, log: Log
) -> DemoView:
    return end_demo(demo_id, product, runner, log)


_BLANK_CLIENT_GRAPH = """\
version: 1
site: client
base_url: https://example.com/
persona:
  product_name: your product
  one_liner: ""
  agent_name: Navigator AI
  tone: friendly, clear, concise
demo_playlist:
  - order: 1
    name: Default walkthrough
    page_id: home
    flow_id: default_walkthrough
pages:
  home:
    name: Home
    url: /
    selectors:
      body: body
    flows:
      default_walkthrough:
        - tool: wait_for
          selector: body
          timeout_ms: 15000
          expects: {check: visible, selector: body}
"""


@app.post("/client/api/bootstrap")
def client_bootstrap(request: Request, registry: Reg) -> dict:
    require_local_ops(request)

    """Ensure a client product + API key exist; persist key across reloads."""
    existing = resolve_client_api_key(settings.client_api_key)
    if existing:
        try:
            product = registry.authenticate(existing)
            settings.client_api_key = existing
            persist_client_key(existing)
            return {
                "ok": True,
                "product_id": product.product_id,
                "api_key": None,
                "message": "already configured",
            }
        except ProductNotFound:
            pass

    # One existing tenant, key lost (reload) → re-issue key, keep their graph/data.
    products = registry.list_products()
    if len(products) == 1:
        product = products[0]
        api_key = registry.rotate_api_key(product.product_id)
        settings.client_api_key = api_key
        persist_client_key(api_key)
        return {
            "ok": True,
            "product_id": product.product_id,
            "api_key": api_key,
            "message": "restored existing client; key saved to .navigator_client_key",
        }

    yaml_text = _BLANK_CLIENT_GRAPH
    try:
        parse_site_graph(yaml_text)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None

    spec = NewProduct(
        name="Your Product",
        product_id=f"client-{secrets.token_hex(3)}",
    )
    try:
        registered = registry.register(spec)
    except RegistryError as exc:
        raise HTTPException(409, str(exc)) from None

    registry.put_site_graph(
        registered.product.product_id, yaml_text, "yaml", publish=True
    )
    settings.client_api_key = registered.api_key
    persist_client_key(registered.api_key)
    return {
        "ok": True,
        "product_id": registered.product.product_id,
        "api_key": registered.api_key,
        "message": "key saved to .navigator_client_key",
    }


def _client_brief_id(registry: Registry, product: Product) -> str:
    """Stable id for bio/knowledge files = product_id (not mutable graph site)."""
    return product.product_id


def apply_base_url_to_yaml(yaml_text: str, base_url: str) -> str:
    import yaml
    from copy import deepcopy
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        raise ValueError("invalid site graph yaml")
    data["base_url"] = base_url
    return yaml.dump(data, sort_keys=False, default_flow_style=False)

class ProductDomainBody(BaseModel):
    base_url: str = Field(min_length=1)


class Tier2Body(BaseModel):
    enabled: bool


class AutonomyModeBody(BaseModel):
    mode: str = Field(pattern="^(guided|adaptive|explorer)$")


class HandoffWebhookBody(BaseModel):
    url: str = ""


class AgentSettingsBody(BaseModel):
    default_language: str | None = None
    extra_languages: list[str] | None = None
    agent_gender: str | None = None
    agent_name: str | None = None
    tone: str | None = None
    tts_provider: str | None = None
    gemini_voice: str | None = None


class AgentProviderKeysBody(BaseModel):
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    fish_api_key: str | None = None


class ProductLoginBody(BaseModel):
    login_url: str = ""
    username: str = ""
    #: None = keep stored password; "" = clear; str = replace.
    password: str | None = None
    include_login_in_default_flow: bool = False


@app.get("/client/api/product-login")
def client_get_product_login(product: DashboardAuthedProduct, vault: Vault) -> dict:
    """Public shape only — never the plaintext password."""
    return vault.public(product.product_id)


@app.put("/client/api/product-login")
def client_put_product_login(
    product: DashboardAuthedProduct, body: ProductLoginBody, vault: Vault
) -> dict:
    try:
        vault.put(
            product.product_id,
            login_url=body.login_url,
            username=body.username,
            password=body.password,
            include_login_in_default_flow=body.include_login_in_default_flow,
        )
    except VaultNotConfigured as exc:
        raise HTTPException(503, str(exc)) from None
    except CredentialVaultError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"ok": True, **vault.public(product.product_id)}


@app.delete("/client/api/product-login")
def client_delete_product_login(product: DashboardAuthedProduct, vault: Vault) -> dict:
    vault.delete(product.product_id)
    return {"ok": True, **vault.public(product.product_id)}


@app.get("/client/api/product-domain")
def client_get_product_domain(product: DashboardAuthedProduct, registry: Reg) -> dict:
    # Latest, not published: the dashboard edits drafts.
    try:
        graph = parse_site_graph(registry.latest_revision(product.product_id).yaml)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return {
        "base_url": graph.base_url,
        "placeholder": "example.com" in (graph.base_url or "").lower(),
    }


@app.get("/client/api/tier2")
def client_get_tier2(product: DashboardAuthedProduct, registry: Reg) -> dict:
    """Per-product opt-in for constrained live Tier-2 fallback. Default OFF."""
    fresh = registry.get(product.product_id)
    return {"enabled": bool(fresh.tier2_enabled)}


@app.put("/client/api/tier2")
def client_put_tier2(
    product: DashboardAuthedProduct, body: Tier2Body, registry: Reg
) -> dict:
    updated = registry.set_tier2_enabled(product.product_id, body.enabled)
    return {"ok": True, "enabled": bool(updated.tier2_enabled)}


@app.get("/client/api/autonomy-mode")
def client_get_autonomy_mode(product: DashboardAuthedProduct, registry: Reg) -> dict:
    fresh = registry.get(product.product_id)
    mode = getattr(fresh, "autonomy_mode", None) or "guided"
    return {
        "mode": mode,
        "tier2_enabled": bool(fresh.tier2_enabled),
        "handoff_webhook_url": getattr(fresh, "handoff_webhook_url", "") or "",
    }


@app.put("/client/api/autonomy-mode")
def client_put_autonomy_mode(
    product: DashboardAuthedProduct, body: AutonomyModeBody, registry: Reg
) -> dict:
    updated = registry.set_autonomy_mode(product.product_id, body.mode)
    return {
        "ok": True,
        "mode": updated.autonomy_mode,
        "tier2_enabled": bool(updated.tier2_enabled),
    }


@app.put("/client/api/handoff-webhook")
def client_put_handoff_webhook(
    product: DashboardAuthedProduct, body: HandoffWebhookBody, registry: Reg
) -> dict:
    updated = registry.set_handoff_webhook(product.product_id, body.url)
    return {"ok": True, "url": updated.handoff_webhook_url}


@app.get("/client/api/agent-settings")
def client_get_agent_settings(
    product: DashboardAuthedProduct, registry: Reg, vault: Vault
) -> dict:
    settings_view = registry.get_agent_settings(product.product_id).model_dump()
    return {**settings_view, **vault.provider_keys_public(product.product_id)}


@app.put("/client/api/agent-settings")
def client_put_agent_settings(
    product: DashboardAuthedProduct, body: AgentSettingsBody, registry: Reg
) -> dict:
    patch = body.model_dump(exclude_none=True)
    if "default_language" in patch and patch["default_language"] not in {"en", "hi"}:
        raise HTTPException(422, "default_language must be en or hi")
    if "agent_gender" in patch and patch["agent_gender"] not in {"female", "male"}:
        raise HTTPException(422, "agent_gender must be female or male")
    if "tts_provider" in patch and patch["tts_provider"] not in {
        "auto",
        "gemini",
        "fish",
        "piper",
    }:
        raise HTTPException(422, "invalid tts_provider")
    merged = registry.set_agent_settings(product.product_id, patch)
    return {"ok": True, **merged.model_dump()}


@app.put("/client/api/agent-provider-keys")
def client_put_agent_provider_keys(
    product: DashboardAuthedProduct, body: AgentProviderKeysBody, vault: Vault
) -> dict:
    try:
        vault.put_provider_keys(
            product.product_id,
            gemini_api_key=body.gemini_api_key,
            groq_api_key=body.groq_api_key,
            fish_api_key=body.fish_api_key,
        )
    except VaultNotConfigured as exc:
        raise HTTPException(503, str(exc)) from None
    except CredentialVaultError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"ok": True, **vault.provider_keys_public(product.product_id)}


@app.get("/client/api/demo-readiness")
def client_demo_readiness(
    product: DashboardAuthedProduct,
    registry: Reg,
    origin: Annotated[str, Query()] = "dashboard_test",
) -> dict:
    from navigator.agent.readiness import assess_demo_readiness

    demo_origin = "public_embed" if origin == "public_embed" else "dashboard_test"
    fresh = registry.get(product.product_id)
    mode = getattr(fresh, "autonomy_mode", None) or "guided"
    return assess_demo_readiness(
        registry,
        product.product_id,
        origin=demo_origin,
        autonomy_mode=mode,
    ).as_dict()


@app.get("/client/api/publish-checklist")
def client_publish_checklist(product: DashboardAuthedProduct, registry: Reg) -> dict:
    from navigator.agent.readiness import assess_demo_readiness

    fresh = registry.get(product.product_id)
    mode = getattr(fresh, "autonomy_mode", None) or "guided"
    readiness = assess_demo_readiness(
        registry, product.product_id, origin="public_embed", autonomy_mode=mode
    )
    eval_score: float | None = None
    eval_path = Path(f"tests/eval/demo_brain/{product.product_id}.yaml")
    if eval_path.is_file():
        try:
            from navigator.agent.eval.runner import load_cases, run_eval
            from navigator.agent.nodes.planning import _flow_texts_for_page
            from navigator.knowledge.context import retrieve_context

            pub_rev = registry.published_revision(product.product_id)
            graph = registry.load_graph(product.product_id, pub_rev)
            page_id = next(iter(graph.pages), "")
            flow_texts = _flow_texts_for_page(
                type("D", (), {"graph": graph, "product_id": product.product_id})(),
                page_id,
            )
            report = run_eval(
                load_cases(eval_path),
                graph=graph,
                page_id=page_id,
                product_id=product.product_id,
                retrieve=retrieve_context,
                flow_texts=flow_texts,
            )
            eval_score = report.score_pct
        except Exception:  # noqa: BLE001
            eval_score = None
    rec = "Guided is safest for visitors."
    if mode == "adaptive":
        rec = "Adaptive needs published flows + indexed knowledge."
    return {
        "readiness": readiness.as_dict(),
        "eval_score_pct": eval_score,
        "autonomy_recommendation": rec,
    }

@app.put("/client/api/product-domain")
def client_put_product_domain(
    product: DashboardAuthedProduct, request: Request, body: ProductDomainBody, registry: Reg
) -> dict:
    if "example.com" in body.base_url.lower():
        raise HTTPException(422, "Please enter your actual product domain")
    from urllib.parse import urlparse
    parsed = urlparse(body.base_url)
    if not parsed.scheme:
        body.base_url = "https://" + body.base_url
        parsed = urlparse(body.base_url)
    normalized = f"{parsed.scheme}://{parsed.netloc}/"
    try:
        rev = registry.latest_revision(product.product_id)
        yaml_text = apply_base_url_to_yaml(rev.yaml, normalized)
        rev = registry.put_site_graph(
            product.product_id, yaml_text, "yaml", publish=False
        )
        graph = parse_site_graph(rev.yaml)
        settings.product_url = normalized
    except Exception as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "ok": True,
        "base_url": graph.base_url,
        "revision": rev.revision,
        "published": rev.published,
        "placeholder": "example.com" in (graph.base_url or "").lower(),
    }

class BioBody(BaseModel):
    fields: list[dict[str, str]]


class KnowledgeBody(BaseModel):
    markdown: str = ""


class FlowsBody(BaseModel):
    playlist: list[dict]


class RecordStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_url: str = Field(min_length=1)
    flow_name: str = Field(min_length=1)
    flow_id: str | None = None
    page_id: str = "dashboard"
    narrate: bool = False
    """Show the mic widget in the recorded page and transcribe the walkthrough."""
    save_mode: str = Field(default="new", pattern="^(new|update)$")
    target_flow_id: str | None = None
    target_flow_name: str | None = None


@app.get("/client/api/site-graph")
def client_get_site_graph(product: DashboardAuthedProduct, registry: Reg) -> dict:
    """The revision the Client is editing -- the latest, draft or published."""
    try:
        rev = registry.latest_revision(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return {
        "yaml": rev.yaml,
        "revision": rev.revision,
        "site": rev.site,
        "published": rev.published,
        "published_revision": product.active_revision,
    }


class SiteGraphBody(BaseModel):
    yaml: str = Field(min_length=1)


@app.post("/client/api/site-graph/clear")
def client_clear_site_graph(product: DashboardAuthedProduct, registry: Reg) -> dict:
    """Reset draft site graph to empty shell + clear demo script metadata."""
    try:
        rev = registry.latest_revision(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    try:
        new_yaml = reset_site_graph_for_explore(rev.yaml)
        rev = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "yaml": new_yaml,
        "revision": rev.revision,
        "site": graph.site,
        "published": False,
        "playlist": playlist_from_graph(graph),
        "cleared": True,
    }


@app.put("/client/api/site-graph")
def client_put_site_graph(
    product: DashboardAuthedProduct, body: SiteGraphBody, registry: Reg, vault: Vault
) -> dict:
    """Save as a draft. End Users keep the published revision until publish."""
    try:
        _reject_login_in_yaml(product.product_id, body.yaml, vault)
        rev = registry.put_site_graph(
            product.product_id, body.yaml, "yaml", publish=False
        )
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "ok": True,
        "revision": rev.revision,
        "site": rev.site,
        "published": False,
    }


def _demo_script_inputs(
    product: DashboardAuthedProduct, registry: Reg, vault: Vault
) -> tuple[Any, Any, dict[str, str], str, bool, dict[str, Any]]:
    """Draft revision graph + bio/knowledge/login context for script composer."""
    rev = registry.latest_revision(product.product_id)
    graph = parse_site_graph(rev.yaml)
    bio_raw = load_bio(product.product_id)
    bio_fields: dict[str, str] = {}
    for f in bio_raw.get("fields") or []:
        if isinstance(f, dict) and f.get("key"):
            bio_fields[str(f["key"])] = str(f.get("value") or "")
    knowledge = load_product_brief(product.product_id)
    if not knowledge.strip():
        try:
            knowledge = load_product_brief(graph.site)
        except Exception:  # noqa: BLE001
            knowledge = ""
    include_login = vault.include_login_in_default_flow(product.product_id)
    stored = graph.demo_script_meta()
    return rev, graph, bio_fields, knowledge, include_login, stored


@app.get("/client/api/site-graph/demo-script")
def client_get_demo_script(
    product: DashboardAuthedProduct,
    registry: Reg,
    vault: Vault,
    flow_id: str | None = None,
) -> dict:
    """Compose full-demo script beats for the current draft revision."""
    try:
        rev, graph, bio_fields, knowledge, include_login, stored = _demo_script_inputs(
            product, registry, vault
        )
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    fid = (flow_id or "").strip() or None
    script = compose_full_demo_script(
        graph,
        product_id=product.product_id,
        knowledge_md=knowledge,
        bio_fields=bio_fields,
        include_login=include_login,
        stored_script=stored,
        flow_id_filter=fid,
    )
    script = merge_manual_overrides(script, stored)
    return {
        "revision": rev.revision,
        "published_revision": product.active_revision,
        "playlist": playlist_from_graph(graph),
        "flow_id": fid,
        **script,
    }


class DemoScriptPatchBody(BaseModel):
    beats: list[dict[str, Any]] = Field(default_factory=list)


@app.patch("/client/api/site-graph/demo-script")
def client_patch_demo_script(
    product: DashboardAuthedProduct,
    body: DemoScriptPatchBody,
    registry: Reg,
    vault: Vault,
) -> dict:
    """Save demo script beat edits to draft `_meta.demo_script` (+ sync flow steps)."""
    try:
        rev, graph, bio_fields, knowledge, include_login, _stored = _demo_script_inputs(
            product, registry, vault
        )
        new_yaml = apply_script_patch(rev.yaml, beats=body.beats, sync_flow_steps=True)
        updated = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    script = compose_full_demo_script(
        graph,
        product_id=product.product_id,
        knowledge_md=knowledge,
        bio_fields=bio_fields,
        include_login=include_login,
        stored_script=graph.demo_script_meta(),
    )
    return {
        "ok": True,
        "revision": updated.revision,
        "published_revision": product.active_revision,
        "playlist": playlist_from_graph(graph),
        **script,
    }


@app.post("/client/api/site-graph/demo-script/regenerate")
def client_regenerate_demo_script(
    product: DashboardAuthedProduct,
    registry: Reg,
    vault: Vault,
    flow_id: str | None = None,
) -> dict:
    """Re-compose demo script; preserve beats with spoken_source=manual."""
    try:
        rev, graph, bio_fields, knowledge, include_login, stored = _demo_script_inputs(
            product, registry, vault
        )
        fid = (flow_id or "").strip() or None
        script = regenerate_demo_script(
            graph,
            product_id=product.product_id,
            knowledge_md=knowledge,
            bio_fields=bio_fields,
            include_login=include_login,
            stored_script=stored,
            flow_id_filter=fid,
        )
        new_yaml = apply_script_patch(
            rev.yaml, beats=script.get("beats") or [], sync_flow_steps=False
        )
        updated = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "ok": True,
        "revision": updated.revision,
        "published_revision": product.active_revision,
        "playlist": playlist_from_graph(graph),
        **script,
    }


@app.post("/client/api/site-graph/publish")
def client_publish_site_graph(
    product: DashboardAuthedProduct,
    registry: Reg,
    vault: Vault,
    revision: Annotated[int | None, Body(embed=True)] = None,
) -> dict:
    """Make a revision live for End Users. Defaults to the latest draft."""
    try:
        target = revision or registry.latest_revision(product.product_id).revision
        rev = registry.get_revision(product.product_id, target)
        _reject_login_in_yaml(product.product_id, rev.yaml, vault)
        updated = registry.activate(product.product_id, target)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    index_summary: dict | None = None
    try:
        graph = registry.load_graph(product.product_id, updated.active_revision)
        from navigator.knowledge.publish_index import index_on_publish

        index_summary = index_on_publish(
            product_id=product.product_id,
            graph=graph,
            revision=updated.active_revision,
            chroma_path=settings.chroma_path,
        ).as_dict()
    except Exception:  # noqa: BLE001
        index_summary = None
    return {
        "ok": True,
        "published_revision": updated.active_revision,
        "index": index_summary,
    }


@app.get("/client/api/bio")
def client_get_bio(product: DashboardAuthedProduct, registry: Reg) -> dict:
    data = load_bio(product.product_id)
    if not any(str(f.get("value") or "").strip() for f in data.get("fields", [])):
        try:
            site = registry.load_graph(product.product_id).site
            alt = load_bio(site)
            if any(str(f.get("value") or "").strip() for f in alt.get("fields", [])):
                data = alt
        except Exception:
            pass
    return data


@app.put("/client/api/bio")
def client_put_bio(product: DashboardAuthedProduct, body: BioBody, registry: Reg) -> dict:
    try:
        return save_bio(product.product_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/client/api/knowledge")
def client_get_knowledge(product: DashboardAuthedProduct, registry: Reg) -> dict:
    text = load_product_brief(product.product_id)
    if not text.strip():
        try:
            site = registry.load_graph(product.product_id).site
            text = load_product_brief(site)
        except Exception:
            pass
    return {"markdown": text}


@app.put("/client/api/knowledge")
def client_put_knowledge(product: DashboardAuthedProduct, body: KnowledgeBody, registry: Reg) -> dict:
    saved = save_product_brief(product.product_id, body.markdown)
    chroma_id = None
    if saved.strip():
        try:
            from navigator.knowledge.publish_index import index_knowledge_draft

            rev = registry.latest_revision(product.product_id).revision
            chroma_id = index_knowledge_draft(
                product_id=product.product_id,
                text=saved,
                revision=rev,
                chroma_path=settings.chroma_path,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[client] chroma ingest skipped: {exc}", flush=True)
    return {"markdown": saved, "chroma_id": chroma_id}


@app.get("/client/api/flows")
def client_get_flows(product: DashboardAuthedProduct, registry: Reg) -> dict:
    try:
        graph = parse_site_graph(registry.latest_revision(product.product_id).yaml)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return {"playlist": playlist_from_graph(graph), "site": graph.site}


@app.put("/client/api/flows")
def client_put_flows(product: DashboardAuthedProduct, body: FlowsBody, registry: Reg) -> dict:
    try:
        rev = registry.latest_revision(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    try:
        new_yaml = apply_playlist_to_yaml(rev.yaml, body.playlist)
        rev = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "playlist": playlist_from_graph(graph),
        "revision": rev.revision,
        "published": False,
    }


class FlowDeleteBody(BaseModel):
    flow_id: str = Field(min_length=1)
    page_id: str | None = None


@app.post("/client/api/flows/clear")
def client_clear_all_flows(product: DashboardAuthedProduct, registry: Reg) -> dict:
    """Reset flows, demo script, and explored pages — fresh shell for Auto-Explore."""
    try:
        rev = registry.latest_revision(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    try:
        new_yaml = reset_site_graph_for_explore(rev.yaml)
        rev = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "yaml": new_yaml,
        "playlist": playlist_from_graph(graph),
        "revision": rev.revision,
        "published": False,
        "cleared": True,
    }


@app.post("/client/api/flows/delete")
def client_delete_flow(
    product: DashboardAuthedProduct, body: FlowDeleteBody, registry: Reg
) -> dict:
    """Remove a flow from the playlist and site-graph draft (unpublished)."""
    try:
        rev = registry.latest_revision(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    try:
        new_yaml = remove_flow_from_yaml(
            rev.yaml, flow_id=body.flow_id, page_id=body.page_id
        )
        rev = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "playlist": playlist_from_graph(graph),
        "revision": rev.revision,
        "published": False,
        "deleted_flow_id": body.flow_id.strip(),
    }


class FlowSemanticsBody(BaseModel):
    flow_id: str = Field(min_length=1)
    purpose: str | None = None
    tags: list[str] | None = None
    triggers: list[str] | None = None
    auto_name: str | None = None


@app.patch("/client/api/flows/semantics")
def client_patch_flow_semantics(
    product: DashboardAuthedProduct, body: FlowSemanticsBody, registry: Reg
) -> dict:
    """Client edits generated purpose / tags / name under `_meta.semantics`."""
    import yaml as _yaml

    try:
        rev = registry.latest_revision(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    raw = _yaml.safe_load(rev.yaml)
    if not isinstance(raw, dict):
        raise HTTPException(422, "site graph must be a mapping")
    meta = raw.setdefault("_meta", {})
    if not isinstance(meta, dict):
        raise HTTPException(422, "_meta must be a mapping")
    bucket = meta.setdefault("semantics", {})
    if not isinstance(bucket, dict):
        raise HTTPException(422, "_meta.semantics must be a mapping")
    fid = body.flow_id.strip()
    entry = bucket.get(fid) if isinstance(bucket.get(fid), dict) else {}
    entry = dict(entry)
    if body.purpose is not None:
        entry["purpose"] = body.purpose.strip()
    if body.tags is not None:
        entry["tags"] = [t.strip() for t in body.tags if t and t.strip()]
    if body.triggers is not None:
        entry["triggers"] = [t.strip() for t in body.triggers if t and t.strip()]
    if body.auto_name is not None:
        entry["auto_name"] = body.auto_name.strip()
    bucket[fid] = entry
    new_yaml = _yaml.safe_dump(raw, sort_keys=False)
    try:
        rev = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "playlist": playlist_from_graph(graph),
        "revision": rev.revision,
        "published": False,
        "flow_id": fid,
        "semantics": entry,
    }


@app.get("/client/api/record")
def client_record_status(product: DashboardAuthedProduct) -> dict:
    return recorder_status()


@app.post("/client/api/record/start")
def client_record_start(
    product: DashboardAuthedProduct,
    body: RecordStartBody,
    vault: Vault,
    request: Request,
) -> dict:
    from navigator.automation.login_match import LoginConfig
    from navigator.automation.record_ws import resolve_record_browser_ws

    def _live_login_config() -> LoginConfig:
        return LoginConfig(login_url=vault.login_url(product.product_id))

    mode = body.save_mode.strip().lower()
    if mode == "update":
        fid = (body.target_flow_id or body.flow_id or "").strip()
        if not fid:
            raise HTTPException(
                422, "target_flow_id required when save_mode is update"
            ) from None
        fname = (body.target_flow_name or body.flow_name).strip()
    else:
        fid = (body.flow_id or None)
        fname = body.flow_name.strip()

    peer = request.client.host if request.client else None
    try:
        browser_ws = resolve_record_browser_ws(
            configured=settings.record_browser_ws,
            path_token=settings.record_ws_path,
            peer_ip=peer,
            record_local=settings.record_local,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    try:
        job = start_recorder(
            start_url=body.start_url.strip(),
            flow_name=fname,
            flow_id=fid,
            headful=settings.headful,
            login_config_fn=_live_login_config,
            narrate=body.narrate,
            save_mode=mode,
            browser_ws=browser_ws,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    return {
        "job_id": job.job_id,
        "flow_id": job.flow_id,
        "flow_name": job.flow_name,
        "active": True,
        "phase": job.phase,
        "narrate": bool(job.narration is not None),
        "save_mode": job.save_mode,
        "browser": "local" if browser_ws else "server",
    }


@app.post("/client/api/record/capture")
def client_record_capture(product: DashboardAuthedProduct) -> dict:
    """Leave Setup: from now on, clicks become flow content."""
    try:
        job = begin_capture()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    return {
        "ok": True,
        "phase": job.phase,
        "setup_discarded": job.setup_discarded
        if job.gate is None
        else job.gate.setup_discarded,
        "steps": len(job.steps),
    }


@app.post("/client/api/record/stop")
def client_record_stop(
    product: DashboardAuthedProduct,
    registry: Reg,
    vault: Vault,
    page_id: Annotated[str, Query()] = "dashboard",
) -> dict:
    try:
        job = stop_recorder()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    try:
        rev = registry.latest_revision(product.product_id)
        persona = parse_site_graph(rev.yaml).effective_persona()
        pid = page_id or "dashboard"
        update = getattr(job, "save_mode", "new") == "update"
        if update:
            resolved = resolve_flow_page_id(rev.yaml, job.flow_id)
            if resolved:
                pid = resolved
        new_yaml = merge_recorded_flow(
            rev.yaml,
            flow_name=job.flow_name,
            flow_id=job.flow_id,
            page_id=pid,
            steps=list(job.steps),
            product_name=persona.product_name,
            base_url=recording_base_url(job.start_url),
            update_existing=update,
            replace_steps=update,
        )
        new_yaml, narrated = _attach_recorded_narration(
            new_yaml,
            job,
            update_existing=update,
            replace_steps=update,
        )
        _reject_login_in_yaml(
            product.product_id,
            new_yaml,
            vault,
            allow_flows=frozenset({(pid, job.flow_id)}),
        )
        rev = registry.put_site_graph(
            product.product_id, new_yaml, "recorded", publish=False
        )
        graph = parse_site_graph(new_yaml)
        playlist = playlist_from_graph(graph)
    except SiteGraphError as exc:
        raise HTTPException(422, f"merge failed: {exc}") from None
    return {
        "ok": True,
        "steps": len(job.steps),
        "error": job.error,
        "flow_id": job.flow_id,
        "playlist": playlist,
        "revision": rev.revision,
        "published": False,
        "flagged": list(getattr(job, "flagged", []) or []),
        "setup_discarded": int(getattr(job, "setup_discarded", 0) or 0),
        "phase": getattr(job, "phase", "done"),
        "narrated_steps": narrated,
    }


def _attach_recorded_narration(
    yaml_text: str,
    job,
    *,
    update_existing: bool = False,
    replace_steps: bool = False,
    prior_steps: int = 0,
) -> tuple[str, int]:
    """Transcribe the host's walkthrough onto the flow they just recorded.

    Always writes step_clicks + timing + placeholder narration so timeline
    playback can run even when STT fails. STT overwrites lines when it succeeds.
    """
    narration = getattr(job, "narration", None)
    if narration is None or not job.steps:
        return yaml_text, 0

    from navigator.automation.explore.runner import (
        _append_narration,
        _append_semantics,
        _attach_meta,
        groq_asker,
    )
    from navigator.automation.narration import (
        compact_timeline,
        narrate_recording,
        placeholder_narration_lines,
        rebuild_flow_narration,
        speech_windows_payload,
        step_timings,
    )
    from navigator.automation.record_scrub import (
        scrub_recorded_steps,
        step_mouse_paths_payload,
    )
    from navigator.core.groq_keys import groq_key_candidates

    scrubbed_steps = scrub_recorded_steps(list(job.steps))
    if not scrubbed_steps:
        return yaml_text, 0

    mouse_paths = step_mouse_paths_payload(scrubbed_steps)
    step_times = [int(getattr(s, "at_ms", 0) or 0) for s in scrubbed_steps]
    lines = placeholder_narration_lines(scrubbed_steps)
    speech: list[dict[str, int]] = []
    used_stt = False
    hints = [
        str(getattr(s, "alias", "") or "").replace("_", " ").strip()
        for s in scrubbed_steps
    ]

    audio = narration.audio()
    keys = groq_key_candidates()
    if audio and keys:
        api_key = keys[0]
        try:
            stt_lines, _stt_timings, _stt_windows = narrate_recording(
                audio=audio,
                steps=scrubbed_steps,
                api_key=api_key,
                ask_text=groq_asker(api_key),
                language=getattr(narration, "language", "auto") or "auto",
                translate_to=getattr(narration, "translate_to", "same") or "same",
            )
            if any(str(l).strip() for l in stt_lines):
                lines = stt_lines
                used_stt = True
        except Exception as exc:  # noqa: BLE001
            print(f"[record] narration STT failed (using placeholders): {exc}", flush=True)
    elif audio and not keys:
        print("[record] narration STT skipped: no Groq API keys", flush=True)

    if not used_stt:
        lines, timings, windows, click_ms = rebuild_flow_narration(
            lines=lines,
            step_times_ms=step_times,
            hints=hints,
            ask_text=None,
        )
    else:
        click_ms, windows = compact_timeline(lines)
        timings = step_timings(click_ms, lines)
    speech = speech_windows_payload(windows)
    clicks = [{"idx": i, "at_ms": int(t)} for i, t in enumerate(click_ms)]

    semantics_payload = {
        "purpose": "",
        "auto_name": job.flow_name,
        "steps": [
            {"idx": i, "description": line}
            for i, line in enumerate(lines)
            if line.strip()
        ],
    }

    if update_existing and not replace_steps:
        yaml_text = _append_narration(yaml_text, job.flow_id, lines)
        yaml_text = _append_semantics(
            yaml_text, job.flow_id, semantics_payload, offset=prior_steps
        )
        for section, rows in (
            ("step_timing", timings),
            ("step_clicks", clicks),
            ("step_speech", speech),
            ("step_mouse_paths", mouse_paths),
        ):
            if rows:
                yaml_text = _append_meta_rows(
                    yaml_text, section, job.flow_id, rows, offset=prior_steps
                )
    else:
        yaml_text = _attach_meta(
            yaml_text, "narration_suggestions", job.flow_id, lines
        )
        yaml_text = _attach_meta(
            yaml_text, "semantics", job.flow_id, semantics_payload
        )
        for section, rows in (
            ("step_timing", timings),
            ("step_clicks", clicks),
            ("step_speech", speech),
            ("step_mouse_paths", mouse_paths),
        ):
            if rows:
                yaml_text = _attach_meta(yaml_text, section, job.flow_id, rows)
    narrated = sum(1 for l in lines if str(l).strip())
    print(
        f"[record] narration: {narrated}/{len(lines)} steps have spoken lines",
        flush=True,
    )
    return yaml_text, narrated


def _append_meta_rows(
    yaml_text: str,
    section: str,
    flow_id: str,
    new_rows: list[dict],
    *,
    offset: int = 0,
) -> str:
    """Update mode: keep prior `idx` rows for this flow, append new ones shifted."""
    import yaml as _yaml

    from navigator.automation.explore.runner import _attach_meta

    raw = _yaml.safe_load(yaml_text)
    prior: list[dict] = []
    if isinstance(raw, dict):
        bucket = (raw.get("_meta") or {}).get(section) or {}
        if isinstance(bucket, dict) and isinstance(bucket.get(flow_id), list):
            prior = [r for r in bucket[flow_id] if isinstance(r, dict)]
    shifted = [
        {**row, "idx": int(row["idx"]) + offset}
        for row in new_rows
        if isinstance(row, dict)
    ]
    return _attach_meta(yaml_text, section, flow_id, prior + shifted)


# -- autonomous exploration ---------------------------------------------------
#
# The second flow-creation path. It produces the same RecordedStep list as the
# manual recorder and merges through the same `merge_recorded_flow` +
# `put_site_graph(publish=False)`, so an explored flow lands in the identical
# review-before-activate gate. Nothing here publishes anything.


class ExploreStartBody(BaseModel):
    base_url: str | None = None
    """Defaults to the stored product login URL / site graph base_url."""
    max_pages: int = Field(default=25, ge=1, le=200)
    max_steps: int = Field(default=120, ge=1, le=1000)
    max_wall_clock_s: float = Field(default=600.0, ge=30.0, le=7200.0)
    answer_timeout_s: float = Field(default=300.0, ge=10.0, le=3600.0)
    save_mode: str = Field(default="new", pattern="^(new|update)$")
    """`new` mints explored_*; `update` overwrites target_flow_id in place."""
    target_flow_id: str | None = None
    target_flow_name: str | None = None
    new_flow_name: str | None = None
    """Display name (and flow_id slug) when save_mode is new."""
    focus_hint: str | None = None
    """Tab, nav label, or feature area to explore first (e.g. Inbox, Billing)."""
    include_paths: list[str] = Field(default_factory=list, max_length=50)
    """When non-empty, explore ONLY URL paths starting with one of these."""
    exclude_paths: list[str] = Field(default_factory=list, max_length=50)
    """URL paths the explorer must never open."""
    exclude_labels: list[str] = Field(default_factory=list, max_length=50)
    """Control labels the explorer must never click (e.g. Logout, Billing)."""


class ExploreAnswerBody(BaseModel):
    qid: str = Field(min_length=1)
    value: str = ""
    skip: bool = False


class ExploreFlaggedBody(BaseModel):
    """Client reviews a guardrail skip: allow for this run, or dismiss from the list."""

    action: str = Field(pattern="^(allow|dismiss)$")
    selector: str = ""
    label: str = ""
    element_key: str = ""


def _explore_base_url(product: Product, registry: Registry, vault: CredentialVault) -> str:
    login_url = (vault.login_url(product.product_id) or "").strip()
    if login_url:
        return login_url
    try:
        return parse_site_graph(registry.latest_revision(product.product_id).yaml).base_url
    except (ProductNotFound, SiteGraphError):
        raise HTTPException(
            400, "no product URL on file — set Product Login or upload a site graph first"
        ) from None


@app.get("/client/api/explore")
def client_explore_status(product: DashboardAuthedProduct, vault: Vault) -> dict:
    from navigator.automation.explore.runner import active_session

    session = active_session()
    status: dict = (
        {"active": False}
        if session is None or session.product_id != product.product_id
        else session.status()
    )
    # public(), not credentials_for(): the dashboard only needs to know a login
    # exists, and this route must not decrypt (or fail on an unconfigured key).
    status["has_credentials"] = bool(vault.public(product.product_id)["has_password"])
    return status


@app.get("/client/api/explore/frame")
def client_explore_frame(product: DashboardAuthedProduct) -> dict:
    """Latest JPEG viewport from the server Chromium running this explore."""
    from navigator.automation.explore.runner import active_session

    session = active_session()
    if session is None or session.product_id != product.product_id:
        raise HTTPException(409, "no active exploration")
    frame = session.frame_payload()
    if frame is None:
        raise HTTPException(404, "no frame yet")
    return frame


@app.post("/client/api/explore/start", status_code=202)
def client_explore_start(
    product: DashboardAuthedProduct, body: ExploreStartBody, registry: Reg, vault: Vault
) -> dict:
    from navigator.automation.explore.runner import start_exploration
    from navigator.automation.explore.session import ExplorationBudget

    base_url = (body.base_url or "").strip() or _explore_base_url(product, registry, vault)
    try:
        persona = parse_site_graph(
            registry.latest_revision(product.product_id).yaml
        ).effective_persona()
        product_name = persona.product_name
    except (ProductNotFound, SiteGraphError):
        raise HTTPException(
            400, "upload or record a site graph before exploring"
        ) from None

    try:
        session = start_exploration(
            product_id=product.product_id,
            base_url=base_url,
            product_name=product_name,
            budget=ExplorationBudget(
                max_pages=body.max_pages,
                max_steps=body.max_steps,
                max_wall_clock_s=body.max_wall_clock_s,
                answer_timeout_s=body.answer_timeout_s,
            ),
            save_mode=body.save_mode,
            target_flow_id=(body.target_flow_id or "").strip(),
            target_flow_name=(body.target_flow_name or "").strip(),
            new_flow_name=(body.new_flow_name or "").strip(),
            focus_hint=(body.focus_hint or "").strip(),
            include_paths=body.include_paths,
            exclude_paths=body.exclude_paths,
            exclude_labels=body.exclude_labels,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    return session.status()


@app.post("/client/api/explore/stop")
def client_explore_stop(product: DashboardAuthedProduct) -> dict:
    from navigator.automation.explore.runner import active_session

    session = active_session()
    if session is None or session.product_id != product.product_id:
        raise HTTPException(409, "no active exploration")
    session.request_stop()
    return session.status()


@app.post("/client/api/explore/answer")
def client_explore_answer(
    product: DashboardAuthedProduct, body: ExploreAnswerBody
) -> dict:
    """Answer the pending business-specific field question; exploration resumes."""
    from navigator.automation.explore.runner import active_session

    session = active_session()
    if session is None or session.product_id != product.product_id:
        raise HTTPException(409, "no active exploration")
    ok = (
        session.skip_question(body.qid)
        if body.skip
        else session.answer(body.qid, body.value)
    )
    if not ok:
        # Stale tab: the question it is answering is no longer the open one.
        raise HTTPException(409, "that question is no longer pending")
    return {"ok": True}


@app.post("/client/api/explore/flagged")
def client_explore_flagged(
    product: DashboardAuthedProduct, body: ExploreFlaggedBody
) -> dict:
    """Allow a skipped control for this explore, or dismiss it from the review list."""
    from navigator.automation.explore.runner import active_session

    session = active_session()
    if session is None or session.product_id != product.product_id:
        raise HTTPException(409, "no active exploration")
    if body.action == "allow":
        ok = session.allow_flagged(
            selector=body.selector, label=body.label, key=body.element_key
        )
    else:
        ok = session.dismiss_flagged(
            selector=body.selector, label=body.label, key=body.element_key
        )
    if not ok:
        raise HTTPException(422, "selector, label, or element_key required")
    return session.status()


@app.post("/client/api/explore/ticket", status_code=201)
def client_explore_ticket(product: DashboardAuthedProduct) -> dict:
    """Mint a single-use, short-lived ticket for the exploration WebSocket.

    The browser WebSocket API cannot set an Authorization header, so the JWT is
    exchanged here (on an authed route) for a one-shot ticket carried in the
    query string. Same shape as the public `sess_` token: single use, hashed
    nowhere it can leak, and useless once redeemed.
    """
    from navigator.automation.explore.tickets import mint_ticket

    return {"ticket": mint_ticket(product.product_id), "expires_in_s": 60}


@app.websocket("/client/api/explore/ws")
async def client_explore_ws(websocket: WebSocket, ticket: str = Query(default="")) -> None:
    """Live exploration channel: replayed log, then events as they happen.

    Read-only. Answers go over the authed POST route, not this socket, so a
    redeemed ticket can never be used to inject a field value.
    """
    import asyncio

    from navigator.automation.explore.runner import active_session
    from navigator.automation.explore.tickets import redeem_ticket

    product_id = redeem_ticket(ticket)
    if product_id is None:
        await websocket.close(code=4401)
        return
    session = active_session()
    if session is None or session.product_id != product_id:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    class _Bridge:
        """The explorer runs on a plain thread; hop each event onto the loop."""

        def put_nowait(self, event: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

    bridge = _Bridge()
    replay = session.add_listener(bridge)
    try:
        await websocket.send_json({"type": "status", **session.status()})
        for event in replay:
            await websocket.send_json(event)
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("type") == "done":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        session.remove_listener(bridge)


@app.get("/client/api/metrics")
def client_metrics(
    product: DashboardAuthedProduct,
    runner: Runner,
    log: Log,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> dict:
    """KPIs for the console. Durable counters from the action log, live state
    from the in-process runner (which is empty after a restart)."""
    metrics = log.dashboard_metrics(product.product_id, days=days)
    in_memory = runner.list(product.product_id)
    im_running = sum(1 for d in in_memory if d.status in ("starting", "running"))
    im_live_running = sum(
        1
        for d in in_memory
        if d.status in ("starting", "running") and d.origin == "public_embed"
    )
    im_test_running = sum(
        1
        for d in in_memory
        if d.status in ("starting", "running") and d.origin == "dashboard_test"
    )
    metrics["demos"]["running"] = max(metrics["demos"]["running"], im_running)
    metrics["live"]["running"] = max(metrics["live"]["running"], im_live_running)
    metrics["test"]["running"] = max(metrics["test"]["running"], im_test_running)
    metrics["run_series"] = metrics["series"]
    return metrics


@app.get("/client/api/runs", response_model=list[DemoRunView])
def client_list_runs(
    product: DashboardAuthedProduct,
    log: Log,
    runner: Runner,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> list[DemoRunView]:
    """Last N days of demo runs; reconcile stale running rows with the live runner."""
    from navigator.logs.store import utcnow

    live = {
        str(h.demo_id): h.status
        for h in runner.list(product.product_id)
        if h.status in ("starting", "running")
    }
    out: list[DemoRunView] = []
    for row in log.list_runs(product.product_id, days=days):
        did = str(row["demo_id"])
        status = row["status"]
        if did in live:
            status = live[did]
        elif status in ("starting", "running"):
            # Ghost row after restart / crash — close it in SQLite.
            log.update_run_status(
                UUID(row["session_id"]), "finished", ended_at=utcnow()
            )
            status = "finished"
        out.append(DemoRunView(**{**row, "status": status}))
    return out


@app.get("/client/api/runs/{session_id}", response_model=DemoRunView)
def client_get_run(
    session_id: UUID, product: DashboardAuthedProduct, log: Log
) -> DemoRunView:
    row = log.get_run(session_id, product.product_id)
    if row is None:
        raise HTTPException(404, "no such run")
    return DemoRunView(**row)


@app.get("/client/api/runs/{session_id}/events", response_model=list[ActionLogEntry])
def client_run_events(
    session_id: UUID, product: DashboardAuthedProduct, log: Log
) -> list[ActionLogEntry]:
    """Full ActionLog for one run — client dashboard only, never spoken."""
    row = log.get_run(session_id, product.product_id)
    entries = log.entries(session_id, product_id=product.product_id)
    if row is None and not entries:
        raise HTTPException(404, "no such run")
    return entries


class DecisionTraceView(BaseModel):
    id: str
    session_id: str
    utterance: str
    branch: str
    chosen_flow_id: str | None = None
    spoken: str
    flow_candidates: list[list[float | str]] = Field(default_factory=list)
    knowledge_hits: list[list[float | str]] = Field(default_factory=list)
    detail: str = ""
    created_at: str


@app.get("/client/api/runs/{session_id}/decisions", response_model=list[DecisionTraceView])
def client_run_decisions(
    session_id: UUID, product: DashboardAuthedProduct, log: Log
) -> list[DecisionTraceView]:
    from navigator.logs.decisions import DecisionTraceStore

    with DecisionTraceStore(settings.db_path) as store:
        rows = store.for_session(session_id, product_id=product.product_id)
    if not rows:
        row = log.get_run(session_id, product.product_id)
        if row is None:
            raise HTTPException(404, "no such run")
    return [
        DecisionTraceView(
            id=r.id,
            session_id=r.session_id,
            utterance=r.utterance,
            branch=r.branch,
            chosen_flow_id=r.chosen_flow_id,
            spoken=r.spoken,
            flow_candidates=[[f, c] for f, c in r.flow_candidates],
            knowledge_hits=[[k, s] for k, s in r.knowledge_hits],
            detail=r.detail,
            created_at=r.created_at,
        )
        for r in rows
    ]


@app.post("/v1/zoom/zak")
def zoom_zak_callback(
    body: Annotated[dict | None, Body()] = None,
    secret: Annotated[str | None, Query()] = None,
) -> dict[str, str]:
    """Attendee callback: mint a fresh ZAK so the bot can start Zoom as host."""
    _ = body  # Attendee sends bot_id / meeting_url; unused for minting
    expected = (settings.zoom_zak_callback_secret or "").strip()
    if expected and secret != expected:
        raise HTTPException(401, "unauthorized")
    try:
        provider = make_provider("zoom")
        if not isinstance(provider, ZoomProvider):
            raise MeetingProviderError("meeting platform is not zoom")
        zak = provider.fetch_zak()
    except MeetingProviderError as exc:
        raise HTTPException(502, str(exc)) from None
    return {"zak_token": zak}
