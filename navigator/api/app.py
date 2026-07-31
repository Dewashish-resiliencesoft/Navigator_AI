"""The wrapper API: one deployment, many products.

A customer registers a product, uploads a site graph, and asks for a demo. The
site graph is the only interface between Navigator and any product, so this layer
adds no product-specific logic whatsoever -- it is a registry, an authenticator,
and a way to start demos.

Run it:
    .venv/bin/uvicorn navigator.api.app:app --reload --workers 1

`--workers 1` matters: live demo state is in-process. See DemoRunner.
"""

from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from navigator.api.registry import (
    NewProduct,
    Product,
    ProductNotFound,
    Registry,
    RegistryError,
    RegisteredProduct,
    SiteGraphRevision,
    SiteGraphSource,
)
from navigator.api.runner import DemoRunner
from navigator.config.site_graph import SiteGraphError
from navigator.logs.store import ActionLog
from navigator.schemas import ActionLogEntry
from navigator.settings import settings

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


_registry = Registry(os.environ.get("NAVIGATOR_REGISTRY_DB", "registry.db"))
_log = ActionLog(settings.db_path)
_runner = DemoRunner(str(settings.db_path), headful=settings.headful)


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
Reg = Annotated[Registry, Depends(get_registry)]
Runner = Annotated[DemoRunner, Depends(get_runner)]
Log = Annotated[ActionLog, Depends(get_log)]


# -- request/response bodies --------------------------------------------------


class SiteGraphUpload(BaseModel):
    yaml: str = Field(min_length=1)
    source: SiteGraphSource = "yaml"


class NewDemo(BaseModel):
    page_id: str
    flow_id: str
    meeting_url: str | None = None
    """Ignored until Phase 3. A demo currently runs against a local browser."""


class DemoView(BaseModel):
    demo_id: UUID
    product_id: str
    revision: int
    session_id: UUID
    status: str
    page_id: str
    actions: int
    failures: int
    error: str | None = None
    said: list[str] = Field(default_factory=list)


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
    upload: SiteGraphUpload, product: AuthedProduct, registry: Reg
) -> SiteGraphRevision:
    """Validate and store a new site graph revision, and make it active.

    Validation runs before anything is written, so a rejected upload cannot break
    a live demo. The error text is identical to what the file loader produces --
    there is one validator in the system, deliberately.
    """
    try:
        return registry.put_site_graph(
            product.product_id, upload.yaml, upload.source
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
    revision: Annotated[int, Body(embed=True)],
) -> Product:
    """Roll back to an earlier revision."""
    try:
        return registry.activate(product.product_id, revision)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None


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
    """Start a demo. Returns immediately; poll GET /v1/demos/{id} for progress."""
    try:
        graph = registry.load_graph(product.product_id)
        revision = product.active_revision or 0
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None

    try:
        graph.flow(spec.page_id, spec.flow_id)  # fail fast on a bad flow
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None

    handle = runner.start(
        product.product_id, graph, revision, (spec.page_id, spec.flow_id)
    )
    return DemoView(**handle.public())


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
def end_demo(demo_id: UUID, product: AuthedProduct, runner: Runner) -> DemoView:
    handle = runner.stop(demo_id, product.product_id)
    if handle is None:
        raise HTTPException(404, "no such demo")
    return DemoView(**handle.public())


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
    from navigator.memory.pending import PendingCorrectionStore

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
    from navigator.memory.pending import PendingCorrectionStore
    from navigator.memory.seed import seed_correction

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
    from navigator.memory.pending import PendingCorrectionStore

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
def ingest_knowledge(body: KnowledgeIngestBody, product: AuthedProduct) -> dict:
    """Add a product_knowledge doc for planning retrieval."""
    from navigator.memory.seed import seed_knowledge

    doc_id = seed_knowledge(
        settings.chroma_path, product_id=product.product_id, text=body.text
    )
    return {"id": doc_id, "product_id": product.product_id}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
