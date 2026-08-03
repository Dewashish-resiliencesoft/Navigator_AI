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
from pathlib import Path
from typing import Annotated, Callable
from uuid import UUID
from datetime import datetime, timezone

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from navigator.client.auth import persist_client_key, resolve_client_api_key
from navigator.client.dashboard import (
    WEB_ASSETS,
    client_index_html,
    require_local_ops,
)
from navigator.client.content import (
    apply_playlist_to_yaml,
    begin_capture,
    merge_recorded_flow,
    playlist_from_graph,
    recorder_status,
    recording_base_url,
    start_recorder,
    stop_recorder,
)
from navigator.knowledge.company_bio import load_bio, save_bio
from navigator.knowledge.product_brief import load_product_brief, save_product_brief
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
import bcrypt
from fastapi import Response, Cookie
from navigator.app.auth_store import AuthStore, AuthError, InvalidCredentials

from navigator.app.session_tokens import SessionTokenStore, SessionTokenError
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

app = FastAPI(
    title="Navigator AI",
    version="0.1.0",
    description="Live interactive demo agent for any web product.",
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


def _reject_login_in_yaml(product_id: str, yaml_text: str, vault: CredentialVault) -> None:
    """Save/activate gate: login steps belong in Product Login, not in flows."""
    from navigator.automation.login_match import LoginConfig, assert_no_login_in_graph

    graph = parse_site_graph(yaml_text, origin=f"product {product_id}")
    assert_no_login_in_graph(
        graph,
        LoginConfig(login_url=vault.login_url(product_id)),
        include_login_in_default_flow=vault.include_login_in_default_flow(product_id),
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


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class SignupRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    product_id: str


def _mint_jwt(user_id: str, product_id: str) -> str:
    now = datetime.now(timezone.utc)
    expires_in = 900
    payload = {
        "sub": user_id,
        "product_id": product_id,
        "role": "admin",
        "exp": int(now.timestamp() + expires_in),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _set_refresh_cookie(response: Response, token: str) -> None:
    # Client console is loopback HTTP — Secure cookies would never stick.
    response.set_cookie(
        key="refresh_token",
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=7 * 24 * 3600,
        path="/",
    )


def _issue_tokens(
    response: Response, store: AuthStore, *, user_id: str, product_id: str
) -> dict:
    access_token = _mint_jwt(user_id, product_id)
    refresh_token = store.create_refresh_token(user_id)
    _set_refresh_cookie(response, refresh_token)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 900,
        "product_id": product_id,
    }


@app.post("/v1/auth/signup", response_model=TokenResponse, status_code=201)
def signup(
    req: SignupRequest,
    response: Response,
    registry: Reg,
    store: Annotated[AuthStore, Depends(get_auth_store)],
) -> dict:
    """Create a new client company (product) + first admin user, return JWT."""
    email = req.email.strip().lower()
    if store.get_user_by_email(email):
        raise HTTPException(409, "email already registered")

    spec = NewProduct(name=req.company_name.strip())
    try:
        registered = registry.register(spec)
    except RegistryError as exc:
        raise HTTPException(409, str(exc)) from None

    product_id = registered.product.product_id
    try:
        # A brand-new tenant has nothing live to protect, and needs a published
        # revision to exist at all before they can run anything.
        registry.put_site_graph(product_id, _BLANK_CLIENT_GRAPH, "yaml", publish=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"could not seed site graph: {exc}") from None

    try:
        user_id = store.create_user(product_id, email, req.password)
    except AuthError as exc:
        raise HTTPException(409, str(exc)) from None

    return _issue_tokens(response, store, user_id=user_id, product_id=product_id)


@app.post("/v1/auth/login", response_model=TokenResponse)
def login(
    req: LoginRequest,
    response: Response,
    store: Annotated[AuthStore, Depends(get_auth_store)],
) -> dict:
    email = req.email.strip().lower()
    user = store.get_user_by_email(email)
    if not user or not bcrypt.checkpw(
        req.password.encode(), user["password_hash"].encode()
    ):
        raise HTTPException(401, "invalid credentials")

    return _issue_tokens(
        response, store, user_id=user["user_id"], product_id=user["product_id"]
    )


@app.post("/v1/auth/refresh", response_model=TokenResponse)
def refresh(
    response: Response,
    store: Annotated[AuthStore, Depends(get_auth_store)],
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> dict:
    if not refresh_token:
        raise HTTPException(401, "no refresh token")

    try:
        user_id = store.consume_refresh_token(refresh_token)
        user = store.get_user(user_id)
        if not user:
            raise HTTPException(401, "invalid user")

        return _issue_tokens(
            response,
            store,
            user_id=user["user_id"],
            product_id=user["product_id"],
        )
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from None


@app.post("/v1/auth/logout")
def logout(
    response: Response,
    store: Annotated[AuthStore, Depends(get_auth_store)],
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> dict:
    if refresh_token:
        store.revoke_refresh_token(refresh_token)
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}


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

    page_id = spec.page_id
    flow_id = spec.flow_id
    if not page_id or not flow_id:
        primary = graph.primary_flow()
        if primary:
            page_id = page_id or primary[0]
            flow_id = flow_id or primary[1]
    page_id = page_id or next(iter(graph.pages), "")
    flow_id = flow_id or settings.live_walkthrough_flow
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
    return RedirectResponse(url="/docs")


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
    start_url: str = Field(min_length=1)
    flow_name: str = Field(min_length=1)
    flow_id: str | None = None
    page_id: str = "dashboard"


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
    return {"ok": True, "published_revision": updated.active_revision}


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
            from navigator.knowledge.memory.seed import seed_knowledge

            chroma_id = seed_knowledge(
                settings.chroma_path, product_id=product.product_id, text=saved
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


@app.get("/client/api/record")
def client_record_status(product: DashboardAuthedProduct) -> dict:
    return recorder_status()


@app.post("/client/api/record/start")
def client_record_start(
    product: DashboardAuthedProduct, body: RecordStartBody, vault: Vault
) -> dict:
    from navigator.automation.login_match import LoginConfig

    def _live_login_config() -> LoginConfig:
        return LoginConfig(login_url=vault.login_url(product.product_id))

    try:
        job = start_recorder(
            start_url=body.start_url.strip(),
            flow_name=body.flow_name.strip(),
            flow_id=(body.flow_id or None),
            headful=settings.headful,
            login_config_fn=_live_login_config,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    return {
        "job_id": job.job_id,
        "flow_id": job.flow_id,
        "flow_name": job.flow_name,
        "active": True,
        "phase": job.phase,
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
        new_yaml = merge_recorded_flow(
            rev.yaml,
            flow_name=job.flow_name,
            flow_id=job.flow_id,
            page_id=page_id or "dashboard",
            steps=list(job.steps),
            product_name=persona.product_name,
            base_url=recording_base_url(job.start_url),
        )
        _reject_login_in_yaml(product.product_id, new_yaml, vault)
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
    }


@app.get("/client/api/metrics")
def client_metrics(
    product: DashboardAuthedProduct,
    runner: Runner,
    log: Log,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> dict:
    """KPIs for the console. Durable counters from the action log, live state
    from the in-process runner (which is empty after a restart)."""
    metrics = log.product_metrics(product.product_id, days=days)
    demos = runner.list(product.product_id)
    metrics["live"] = {
        "total": len(demos),
        "running": sum(1 for d in demos if d.status in ("starting", "running")),
        "failed": sum(1 for d in demos if d.status == "failed"),
    }
    return metrics


@app.get("/client/api/runs", response_model=list[DemoRunView])
def client_list_runs(
    product: DashboardAuthedProduct,
    log: Log,
    runner: Runner,
    days: Annotated[int, Query(ge=1, le=7)] = 7,
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
