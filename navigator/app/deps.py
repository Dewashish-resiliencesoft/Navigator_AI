"""App lifespan, singletons, and auth dependencies for Navigator API routes."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated, Callable

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException

from navigator.app.api_models import IntakePrefill
from navigator.app.credential_vault import CredentialVault
from navigator.app.registry import Product, ProductNotFound, Registry
from navigator.app.runner import DemoRunner
from navigator.auth import AuthStore, SessionTokenStore, SessionTokenError
from navigator.core.settings import settings
from navigator.logs.store import ActionLog
from navigator.meeting.providers import (
    MeetingProvider,
    Platform as MeetingPlatform,
    make_provider,
)

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    from navigator.automation.playwright_env import ensure_playwright_browsers
    from navigator.meeting.attendee_stack import ensure_attendee_stack

    ensure_playwright_browsers()
    ensure_attendee_stack()
    yield

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
