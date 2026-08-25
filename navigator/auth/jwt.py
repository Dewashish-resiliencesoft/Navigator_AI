"""JWT access tokens + refresh cookie helpers for dashboard auth."""

from __future__ import annotations

from datetime import datetime, timezone

import jwt
from fastapi import Response

from navigator.auth.store import AuthStore
from navigator.core.settings import settings

ACCESS_TOKEN_TTL_S = 900


def mint_access_token(*, user_id: str, product_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "product_id": product_id,
        "role": "admin",
        "exp": int(now.timestamp() + ACCESS_TOKEN_TTL_S),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="refresh_token",
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=7 * 24 * 3600,
        path="/",
    )


def issue_tokens(
    response: Response, store: AuthStore, *, user_id: str, product_id: str
) -> dict:
    access_token = mint_access_token(user_id=user_id, product_id=product_id)
    refresh_token = store.create_refresh_token(user_id)
    set_refresh_cookie(response, refresh_token)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_TTL_S,
        "product_id": product_id,
    }
