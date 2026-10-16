"""Stable OpenAPI operation ids and method order.

FastAPI keeps each route's methods in a set. Set order follows the process
hash seed, so a route registered for several verbs gets a different
``operationId`` suffix and a different path-item key order in every process.
``python -m navigator.docs check`` then reports a stale spec at random.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from fastapi import FastAPI
from fastapi.routing import APIRoute

#: First match wins. GET stays the id for any route that also accepts GET,
#: which is what a single-method ``@router.get`` already produces.
_METHOD_RANK = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE")


class _StableMethods(set[str]):
    """A set whose iteration order does not depend on PYTHONHASHSEED."""

    def __iter__(self):
        rank = {name: index for index, name in enumerate(_METHOD_RANK)}
        return iter(
            sorted(
                set.__iter__(self),
                key=lambda method: (rank.get(method, len(_METHOD_RANK)), method),
            )
        )


def stable_unique_id(route: APIRoute) -> str:
    """Same shape as FastAPI's default id, with a hash-stable method suffix."""
    operation_id = re.sub(r"\W", "_", f"{route.name}{route.path_format}")
    methods = list(_StableMethods(route.methods or ()))
    if methods:
        operation_id = f"{operation_id}_{methods[0].lower()}"
    return operation_id


def install_stable_openapi(app: FastAPI) -> None:
    """Pin method order before FastAPI builds (and caches) the schema."""

    def openapi() -> dict:
        _pin_method_order(app)
        return FastAPI.openapi(app)

    app.openapi = openapi  # type: ignore[method-assign]


def _pin_method_order(app: FastAPI) -> None:
    # Effective routes are copied when a router is included. Pin those copies:
    # that is the set ``get_openapi`` iterates.
    from fastapi.routing import _EffectiveRouteContext, _IncludedRouter

    def visit(routes: Iterable[object]) -> None:
        for route in routes:
            if isinstance(route, _IncludedRouter):
                for candidate in route.effective_candidates():
                    if isinstance(candidate, _IncludedRouter):
                        visit([candidate])
                    elif isinstance(candidate, _EffectiveRouteContext):
                        _replace_methods(candidate)
                continue
            if isinstance(route, APIRoute):
                _replace_methods(route)

    visit(app.router.routes)


def _replace_methods(route: object) -> None:
    methods = getattr(route, "methods", None)
    if isinstance(methods, set) and not isinstance(methods, _StableMethods):
        route.methods = _StableMethods(methods)  # type: ignore[attr-defined]
