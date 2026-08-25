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

from fastapi import FastAPI

from navigator.app.api_models import *  # noqa: F403
from navigator.app.deps import (  # noqa: F401
    AuthedOrSession,
    AuthedProduct,
    DashboardAuthedProduct,
    Log,
    Providers,
    Reg,
    Runner,
    Vault,
    _auth_store,
    _lifespan,
    _log,
    _registry,
    _runner,
    _token_store,
    _vault,
    authed,
    authed_or_session,
    dashboard_authed,
    get_auth_store,
    get_log,
    get_provider_factory,
    get_registry,
    get_runner,
    get_token_store,
    get_vault,
)
from navigator.app.route_helpers import (  # noqa: F401
    _reject_login_in_yaml,
    _run_live_demo,
    apply_base_url_to_yaml,
)
from navigator.app.routers.client_api import _attach_recorded_narration  # noqa: F401
from navigator.app.routers import client_api, public, v1
from navigator.auth.routes import build_auth_router
from navigator.core.settings import settings  # noqa: F401 — tests patch app_module.settings
from navigator.meeting.providers import make_provider  # noqa: F401 — tests patch this

app = FastAPI(
    title="Navigator AI",
    version="0.1.0",
    description="Live interactive demo agent for any web product.",
    lifespan=_lifespan,
)

app.include_router(
    build_auth_router(get_registry=get_registry, get_auth_store=get_auth_store)
)
app.include_router(v1.router)
app.include_router(client_api.router)
app.include_router(public.router)
public.mount_client_assets(app)
