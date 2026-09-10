"""Public /v1/* API routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from navigator.app.api_models import (
    DemoView,
    KnowledgeIngestBody,
    LiveDemoView,
    NewDemo,
    SessionTokenRequest,
    SessionTokenResponse,
    SiteGraphUpload,
    StartLiveDemo,
)
from navigator.app.deps import (
    AuthedOrSession,
    AuthedProduct,
    Log,
    Providers,
    Reg,
    Runner,
    Vault,
    get_token_store,
)
from navigator.app.registry import (
    NewProduct,
    Product,
    ProductNotFound,
    RegisteredProduct,
    RegistryError,
    SiteGraphRevision,
)
from navigator.app.route_helpers import _reject_login_in_yaml, _run_live_demo
from navigator.auth import SessionTokenStore
from navigator.core.schemas import ActionLogEntry
from navigator.core.settings import settings
from navigator.knowledge.site_graph import SiteGraphError
from navigator.meeting.providers import MeetingProviderError, ZoomProvider

router = APIRouter()

@router.post("/v1/products", response_model=RegisteredProduct, status_code=201)
def register_product(spec: NewProduct, registry: Reg) -> RegisteredProduct:
    """Register a product. The API key in the response is shown exactly once."""
    try:
        return registry.register(spec)
    except RegistryError as exc:
        raise HTTPException(409, str(exc)) from None

@router.get("/v1/products/me", response_model=Product)
def whoami(product: AuthedProduct) -> Product:
    return product

@router.put("/v1/products/site-graph", response_model=SiteGraphRevision, status_code=201)
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

@router.get("/v1/products/site-graph", response_model=SiteGraphRevision)
def get_site_graph(
    product: AuthedProduct,
    registry: Reg,
    revision: Annotated[int | None, Query()] = None,
) -> SiteGraphRevision:
    try:
        return registry.get_revision(product.product_id, revision)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None

@router.get("/v1/products/site-graph/revisions", response_model=list[SiteGraphRevision])
def list_revisions(product: AuthedProduct, registry: Reg) -> list[SiteGraphRevision]:
    return registry.revisions(product.product_id)

@router.post("/v1/products/site-graph/activate", response_model=Product)
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

@router.get("/v1/products/flows")
def list_flows(product: AuthedProduct, registry: Reg) -> dict[str, list[str]]:
    """Which flows this product's active site graph offers, per page."""
    try:
        graph = registry.load_graph(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return {pid: sorted(page.flows) for pid, page in graph.pages.items()}

@router.post("/v1/demos", response_model=DemoView, status_code=202)
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

@router.post("/v1/session-tokens", response_model=SessionTokenResponse, status_code=201)
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

@router.post("/v1/demos/start", response_model=LiveDemoView, status_code=202)
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

@router.get("/v1/demos/{demo_id}", response_model=DemoView)
def get_demo(demo_id: UUID, product: AuthedProduct, runner: Runner) -> DemoView:
    handle = runner.get(demo_id, product.product_id)
    if handle is None:
        raise HTTPException(404, "no such demo")
    return DemoView(**handle.public())

@router.get("/v1/demos", response_model=list[DemoView])
def list_demos(product: AuthedProduct, runner: Runner) -> list[DemoView]:
    return [DemoView(**h.public()) for h in runner.list(product.product_id)]

@router.post("/v1/demos/{demo_id}/end", response_model=DemoView)
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

@router.get("/v1/demos/{demo_id}/actions", response_model=list[ActionLogEntry])
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

@router.get("/v1/products/failures", response_model=list[ActionLogEntry])
def product_failures(
    product: AuthedProduct,
    log: Log,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[ActionLogEntry]:
    """Recent failures across all demos of this product -- which flows are rotting."""
    return log.product_failures(product.product_id, limit)

@router.post("/v1/products/knowledge", status_code=201)
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

@router.post("/v1/zoom/zak")
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
        # Late import so tests can monkeypatch navigator.app.main.make_provider.
        from navigator.app.main import make_provider

        provider = make_provider("zoom")
        if not isinstance(provider, ZoomProvider):
            raise MeetingProviderError("meeting platform is not zoom")
        zak = provider.fetch_zak()
    except MeetingProviderError as exc:
        raise HTTPException(502, str(exc)) from None
    return {"zak_token": zak}


@router.api_route("/v1/live-http/{token}/{path:path}", methods=["GET", "HEAD"])
@router.api_route("/v1/live-http/{token}", methods=["GET", "HEAD"])
def live_http_proxy(
    token: str,
    request: Request,
    path: str = "",
) -> Response:
    """Reverse-proxy screenshare relay through the stable public API origin."""
    from urllib.error import HTTPError, URLError
    from urllib.request import Request as UrlRequest
    from urllib.request import urlopen

    from navigator.meeting.public_mount import lookup_http

    target = lookup_http(token)
    if target is None:
        raise HTTPException(404, "unknown live relay")
    host, port = target
    rel = (path or "").lstrip("/")
    qs = request.url.query
    url = f"http://{host}:{port}/{rel}"
    if qs:
        url = f"{url}?{qs}"
    try:
        req = UrlRequest(url, method=request.method)
        with urlopen(req, timeout=30) as resp:
            body = resp.read()
            status = getattr(resp, "status", 200)
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
            headers = {"Cache-Control": resp.headers.get("Cache-Control", "no-store")}
            return Response(content=body, status_code=int(status), media_type=ctype, headers=headers)
    except HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        return Response(content=body, status_code=int(exc.code))
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(502, f"live relay upstream failed: {exc}") from None


@router.websocket("/v1/live-ws/{token}")
async def live_ws_proxy(websocket: WebSocket, token: str) -> None:
    """Bridge Attendee audio websocket through the stable public API origin."""
    import asyncio

    from navigator.meeting.public_mount import lookup_ws

    target = lookup_ws(token)
    if target is None:
        # Accept then close — rejecting pre-accept shows up as opaque 403 to clients.
        await websocket.accept()
        await websocket.close(code=4404)
        return
    host, port = target
    await websocket.accept()
    print(f"[audio] live-ws proxy accept token={token[:8]}… → {host}:{port}", flush=True)
    try:
        import websockets
    except ImportError:
        await websocket.close(code=1011)
        return
    uri = f"ws://{host}:{port}"
    try:
        async with websockets.connect(uri, max_size=8 * 1024 * 1024) as upstream:
            print(f"[audio] live-ws upstream connected {uri}", flush=True)
            async def client_to_upstream() -> None:
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if "bytes" in msg and msg["bytes"] is not None:
                            await upstream.send(msg["bytes"])
                        elif "text" in msg and msg["text"] is not None:
                            await upstream.send(msg["text"])
                except WebSocketDisconnect:
                    return

            async def upstream_to_client() -> None:
                try:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(str(message))
                except Exception:  # noqa: BLE001
                    return

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                _ = task.exception() if not task.cancelled() else None
    except Exception:  # noqa: BLE001
        try:
            await websocket.close(code=1011)
        except Exception:  # noqa: BLE001
            pass

