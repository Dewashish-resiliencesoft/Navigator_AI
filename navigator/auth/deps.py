"""FastAPI dependencies for dashboard JWT sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException

from navigator.app.registry import Product, ProductNotFound, Registry
from navigator.core.settings import settings


@dataclass(frozen=True)
class DashboardSession:
    user_id: str
    product: Product


def dashboard_session(
    registry: Registry,
    authorization: str | None = None,
) -> DashboardSession:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "expected: Authorization: Bearer <jwt>")

    token = authorization.split(None, 1)[1].strip()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        user_id = str(payload.get("sub") or "")
        product_id = payload.get("product_id")
        if not user_id or not product_id:
            raise HTTPException(401, "invalid JWT payload")
        return DashboardSession(user_id=user_id, product=registry.get(product_id))
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(401, "token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, "invalid token") from exc
    except ProductNotFound as exc:
        raise HTTPException(401, "product not found") from exc


def get_dashboard_session(
    registry: Annotated[Registry, Depends(lambda: None)],  # patched in router mount
    authorization: Annotated[str | None, Header()] = None,
) -> DashboardSession:
    raise RuntimeError("get_dashboard_session must be wired with Registry dependency")


def make_dashboard_session_dep(get_registry):
    def _dep(
        registry: Annotated[Registry, Depends(get_registry)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> DashboardSession:
        return dashboard_session(registry, authorization)

    return _dep
