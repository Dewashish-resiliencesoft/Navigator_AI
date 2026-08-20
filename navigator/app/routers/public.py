"""Public HTML / health surfaces (not /client/api)."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from navigator.client.dashboard import WEB_ASSETS, client_index_html, require_local_ops

router = APIRouter()

@router.get("/")
def root() -> RedirectResponse:
    """Landing URL when the API starts — Client dashboard, not OpenAPI docs."""
    return RedirectResponse(url="/client", status_code=307)

@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}

@router.get("/client", response_class=HTMLResponse)
def client_console(request: Request) -> HTMLResponse:
    require_local_ops(request)
    return HTMLResponse(client_index_html())

@router.get("/ops")
def legacy_ops_redirect() -> RedirectResponse:
    return RedirectResponse(url="/client", status_code=307)


def mount_client_assets(app: FastAPI) -> None:
    """Mount Vite-built client assets when the directory exists."""
    if WEB_ASSETS.is_dir():
        # Vite emits content-hashed filenames, so these are safe to cache hard.
        app.mount("/client/assets", StaticFiles(directory=WEB_ASSETS), name="client-assets")
