"""HTTP routes for dashboard authentication and user preferences."""

from typing import Annotated, Callable

import bcrypt
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from navigator.app.registry import NewProduct, Registry, RegistryError
from navigator.auth.deps import DashboardSession, dashboard_session
from navigator.auth.jwt import issue_tokens
from navigator.auth.store import AuthError, AuthStore

_BLANK_CLIENT_GRAPH = """\
version: 1
site: your-product
base_url: https://example.com/

persona:
  product_name: your product
  one_liner: describe your product in one line
  agent_name: Navigator
  tone: friendly and concise

pages:
  home:
    name: Home
    url: /
    selectors: {}
    flows: {}
"""


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


class UserPreferencesBody(BaseModel):
    hide_get_started_card: bool | None = None
    onboarding_wizard_dismissed: bool | None = None
    onboarding_wizard_completed: bool | None = None


class UserPreferencesView(BaseModel):
    hide_get_started_card: bool
    onboarding_wizard_dismissed: bool
    onboarding_wizard_completed: bool


def build_auth_router(
    *,
    get_registry: Callable,
    get_auth_store: Callable[[], AuthStore],
) -> APIRouter:
    router = APIRouter(tags=["auth"])

    def _session_dep(
        registry: Registry = Depends(get_registry),
        authorization: Annotated[str | None, Header()] = None,
    ) -> DashboardSession:
        return dashboard_session(registry, authorization)

    @router.post("/v1/auth/signup", response_model=TokenResponse, status_code=201)
    def signup(
        req: SignupRequest,
        response: Response,
        registry: Registry = Depends(get_registry),
        store: AuthStore = Depends(get_auth_store),
    ) -> dict:
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
            registry.put_site_graph(product_id, _BLANK_CLIENT_GRAPH, "yaml", publish=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"could not seed site graph: {exc}") from None

        try:
            user_id = store.create_user(product_id, email, req.password)
        except AuthError as exc:
            raise HTTPException(409, str(exc)) from None

        return issue_tokens(
            response, store, user_id=user_id, product_id=product_id
        )

    @router.post("/v1/auth/login", response_model=TokenResponse)
    def login(
        req: LoginRequest,
        response: Response,
        store: AuthStore = Depends(get_auth_store),
    ) -> dict:
        email = req.email.strip().lower()
        user = store.get_user_by_email(email)
        if not user or not bcrypt.checkpw(
            req.password.encode(), user["password_hash"].encode()
        ):
            raise HTTPException(401, "invalid credentials")

        return issue_tokens(
            response,
            store,
            user_id=user["user_id"],
            product_id=user["product_id"],
        )

    @router.post("/v1/auth/refresh", response_model=TokenResponse)
    def refresh(
        response: Response,
        store: AuthStore = Depends(get_auth_store),
        refresh_token: Annotated[str | None, Cookie()] = None,
    ) -> dict:
        if not refresh_token:
            raise HTTPException(401, "no refresh token")

        try:
            user_id = store.consume_refresh_token(refresh_token)
        except AuthError as exc:
            raise HTTPException(401, str(exc)) from None

        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(401, "user not found")

        return issue_tokens(
            response,
            store,
            user_id=user_id,
            product_id=user["product_id"],
        )

    @router.post("/v1/auth/logout")
    def logout(
        response: Response,
        store: AuthStore = Depends(get_auth_store),
        refresh_token: Annotated[str | None, Cookie()] = None,
    ) -> dict:
        if refresh_token:
            store.revoke_refresh_token(refresh_token)
        response.delete_cookie("refresh_token", path="/")
        return {"ok": True}

    @router.get("/client/api/user/preferences", response_model=UserPreferencesView)
    def get_user_preferences(
        session: DashboardSession = Depends(_session_dep),
        store: AuthStore = Depends(get_auth_store),
    ) -> UserPreferencesView:
        prefs = store.get_preferences(session.user_id)
        return UserPreferencesView(**prefs)

    @router.put("/client/api/user/preferences", response_model=UserPreferencesView)
    def put_user_preferences(
        body: UserPreferencesBody,
        session: DashboardSession = Depends(_session_dep),
        store: AuthStore = Depends(get_auth_store),
    ) -> UserPreferencesView:
        patch = body.model_dump(exclude_none=True)
        if not patch:
            prefs = store.get_preferences(session.user_id)
        else:
            prefs = store.set_preferences(session.user_id, patch)
        return UserPreferencesView(**prefs)

    return router
