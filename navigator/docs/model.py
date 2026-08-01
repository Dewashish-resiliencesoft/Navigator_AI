"""Extract everything the docs need from live code.

Nothing here is hand-maintained prose about behaviour. Routes come from FastAPI's
own schema, the site graph format from the Pydantic model, the tool and
postcondition tables from the actual Literal types. Add an endpoint or a new check
kind and the docs follow, because there is nowhere else for the truth to live.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass, field
from typing import Any, get_args

from navigator.knowledge.site_graph import PageSpec, SiteGraph
from navigator.core.schemas import (
    CheckKind,
    ClickElement,
    FillField,
    Navigate,
    Persona,
    Postcondition,
    WaitFor,
)

TOOL_MODELS = (ClickElement, FillField, Navigate, WaitFor)


@dataclass(frozen=True)
class Param:
    name: str
    kind: str
    """query | path | header | body"""
    required: bool
    type_: str
    description: str = ""


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    summary: str
    description: str
    params: tuple[Param, ...] = ()
    request_example: str | None = None
    status: str = "200"
    authed: bool = True

    @property
    def anchor(self) -> str:
        slug = self.path.strip("/").replace("/", "-").replace("{", "").replace("}", "")
        return f"{self.method.lower()}-{slug}"


@dataclass(frozen=True)
class Field_:
    name: str
    type_: str
    required: bool
    default: str
    description: str


@dataclass(frozen=True)
class ModelDoc:
    name: str
    description: str
    fields: tuple[Field_, ...]


@dataclass
class DocsModel:
    """Everything the renderers need. Built once, rendered many ways."""

    title: str
    version: str
    endpoints: list[Endpoint] = field(default_factory=list)
    models: list[ModelDoc] = field(default_factory=list)
    check_kinds: list[tuple[str, str]] = field(default_factory=list)
    tools: list[ModelDoc] = field(default_factory=list)
    openapi: dict[str, Any] = field(default_factory=dict)


# --- OpenAPI -----------------------------------------------------------------


def _type_name(schema: dict) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in schema:
        inner = [_type_name(s) for s in schema["anyOf"] if s.get("type") != "null"]
        return " | ".join(inner) or "null"
    if schema.get("type") == "array":
        return f"{_type_name(schema.get('items', {}))}[]"
    if "enum" in schema:
        return " | ".join(repr(v) for v in schema["enum"])
    return schema.get("type", "object")


def _body_example(operation: dict, components: dict) -> str | None:
    """A JSON skeleton for a request body, built from its schema."""
    body = operation.get("requestBody", {})
    schema = (
        body.get("content", {}).get("application/json", {}).get("schema", {})
    )
    if not schema:
        return None

    resolved = _resolve(schema, components)
    props = resolved.get("properties", {})
    if not props:
        return None

    import json

    example = {
        name: _placeholder(_resolve(prop, components), name)
        for name, prop in props.items()
    }
    return json.dumps(example, indent=2)


def _resolve(schema: dict, components: dict) -> dict:
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return components.get(name, {})
    for key in ("anyOf", "allOf"):
        if key in schema:
            for option in schema[key]:
                if option.get("type") != "null":
                    return _resolve(option, components)
    return schema


def _placeholder(schema: dict, name: str) -> Any:
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    if kind == "object":
        return {}
    return f"<{name}>"


def endpoints_from_openapi(spec: dict) -> list[Endpoint]:
    components = spec.get("components", {}).get("schemas", {})
    out: list[Endpoint] = []

    for path, methods in spec.get("paths", {}).items():
        for method, operation in methods.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue

            params = tuple(
                Param(
                    name=p["name"],
                    kind=p["in"],
                    required=p.get("required", False),
                    type_=_type_name(p.get("schema", {})),
                    description=p.get("description", ""),
                )
                for p in operation.get("parameters", [])
                # The auth header is documented once globally, not per route.
                if p["name"].lower() != "authorization"
            )

            doc = (operation.get("description") or "").strip()
            success = next(
                (c for c in operation.get("responses", {}) if c.startswith("2")),
                "200",
            )

            out.append(
                Endpoint(
                    method=method.upper(),
                    path=path,
                    summary=operation.get("summary") or path,
                    description=doc,
                    params=params,
                    request_example=_body_example(operation, components),
                    status=success,
                    authed=path not in {"/healthz", "/v1/products"},
                )
            )

    out.sort(key=lambda e: (_section_order(e.path), e.path, e.method))
    return out


def _section_order(path: str) -> int:
    if path.startswith("/v1/products/site-graph"):
        return 1
    if path.startswith("/v1/demos"):
        return 2
    if path.startswith("/v1/products"):
        return 0
    return 3


# --- Pydantic models ---------------------------------------------------------


def _model_doc(model: type, name: str | None = None) -> ModelDoc:
    fields: list[Field_] = []
    for fname, info in model.model_fields.items():
        annotation = info.annotation
        type_name = getattr(annotation, "__name__", None) or str(annotation)
        type_name = type_name.replace("typing.", "").replace("NoneType", "null")
        if typing.get_origin(annotation) is not None:
            args = [
                getattr(a, "__name__", str(a)).replace("NoneType", "null")
                for a in get_args(annotation)
            ]
            type_name = f"{typing.get_origin(annotation).__name__}[{', '.join(args)}]"

        default = "required"
        if not info.is_required():
            value = info.get_default(call_default_factory=True)
            default = "—" if value is None else repr(value)

        fields.append(
            Field_(
                name=fname,
                type_=type_name,
                required=info.is_required(),
                default=default,
                description=(info.description or "").strip(),
            )
        )

    return ModelDoc(
        name=name or model.__name__,
        description=(model.__doc__ or "").strip(),
        fields=tuple(fields),
    )


CHECK_DESCRIPTIONS = {
    "visible": "Element is present and visible.",
    "hidden": "Element is absent, or present but not visible.",
    "text_contains": "Element's text contains <code>expected</code>.",
    "value_equals": "Input's value equals <code>expected</code> exactly.",
    "url_matches": "Page URL contains <code>expected</code>. The only check that needs no selector.",
    "element_count": "Exactly <code>expected</code> visible elements match the selector.",
}


def build(app: Any | None = None) -> DocsModel:
    """Assemble the docs model from live code."""
    if app is None:
        from navigator.app.main import app as default_app

        app = default_app

    spec = app.openapi()

    return DocsModel(
        title=spec["info"]["title"],
        version=spec["info"]["version"],
        endpoints=endpoints_from_openapi(spec),
        models=[
            _model_doc(SiteGraph),
            _model_doc(PageSpec),
            _model_doc(Persona),
            _model_doc(Postcondition),
        ],
        check_kinds=[
            (kind, CHECK_DESCRIPTIONS.get(kind, "")) for kind in get_args(CheckKind)
        ],
        tools=[_model_doc(m) for m in TOOL_MODELS],
        openapi=spec,
    )
