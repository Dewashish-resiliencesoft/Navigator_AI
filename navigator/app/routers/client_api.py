"""Dashboard /client/api/* routes."""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from navigator.client.auth import persist_client_key, resolve_client_api_key
from navigator.client.dashboard import require_local_ops
from navigator.client.content import (
    apply_playlist_to_yaml,
    reset_site_graph_for_explore,
    begin_capture,
    merge_recorded_flow,
    playlist_from_graph,
    recorder_status,
    recording_base_url,
    remove_flow_from_yaml,
    resolve_flow_page_id,
    start_recorder,
    stop_recorder,
)
from navigator.knowledge.company_bio import load_bio, save_bio
from navigator.knowledge.product_brief import load_product_brief, save_product_brief
from navigator.knowledge.demo_script import (
    apply_script_patch,
    compose_full_demo_script,
    merge_manual_overrides,
    regenerate_demo_script,
)
from navigator.app.api_models import (
    AgentProviderKeysBody,
    AgentSettingsBody,
    AutonomyModeBody,
    BioBody,
    DecisionTraceView,
    DemoRunView,
    DemoScriptPatchBody,
    DemoView,
    FlowDeleteBody,
    FlowSemanticsBody,
    FlowsBody,
    HandoffWebhookBody,
    KnowledgeBody,
    LiveDemoView,
    ProductDomainBody,
    ProductExploreStartBody,
    ProductLoginBody,
    ProviderModelsBody,
    RecordStartBody,
    SiteGraphBody,
    StartLiveDemo,
    SystemMetrics,
    Tier2Body,
)
from navigator.app.credential_vault import (
    CredentialVault,
    CredentialVaultError,
    VaultNotConfigured,
)
from navigator.app.deps import (
    DashboardAuthedProduct,
    Log,
    Providers,
    Reg,
    Runner,
    Vault,
    get_vault,
)
from navigator.app.registry import (
    NewProduct,
    Product,
    ProductNotFound,
    Registry,
    RegistryError,
)
from navigator.app.route_helpers import (
    _reject_login_in_yaml,
    _run_live_demo,
    apply_base_url_to_yaml,
)
from navigator.app.routers.v1 import end_demo, get_demo, list_demos
from navigator.app.auth_store import InvalidCredentials
from navigator.auth import AuthError
from navigator.core.schemas import ActionLogEntry
from navigator.core.settings import settings
from navigator.knowledge.site_graph import SiteGraphError, parse_site_graph
from navigator.logs.store import ActionLog

router = APIRouter()

@router.get("/client/api/system/health", response_model=SystemMetrics)
def client_system_health(
    product: DashboardAuthedProduct,
    registry: Reg,
    runner: Runner,
    vault: Vault,
) -> SystemMetrics:
    """Real host metrics + Navigator service status for the Client dashboard."""
    from navigator.app.system_health import collect_system_health

    payload = collect_system_health(
        product_id=product.product_id,
        registry=registry,
        runner=runner,
        db_path=str(settings.db_path),
        vault=vault,
    )
    return SystemMetrics(**payload)

@router.post("/client/api/demos/start", response_model=LiveDemoView, status_code=202)
def client_start_live_demo(
    product: DashboardAuthedProduct,
    spec: StartLiveDemo,
    registry: Reg,
    runner: Runner,
    providers: Providers,
) -> LiveDemoView:
    """A TEST demo: the Client checking their own setup. Not billable.

    JWT-only, so an End User cannot reach it, and it runs the Client's latest
    revision (draft included) rather than the published one.
    """
    return _run_live_demo(
        spec, product, None, registry, runner, providers, origin="dashboard_test"
    )

@router.get("/client/api/demos", response_model=list[DemoView])
def client_list_demos(product: DashboardAuthedProduct, runner: Runner) -> list[DemoView]:
    return list_demos(product, runner)

@router.get("/client/api/demos/{demo_id}", response_model=DemoView)
def client_get_demo(demo_id: UUID, product: DashboardAuthedProduct, runner: Runner) -> DemoView:
    return get_demo(demo_id, product, runner)

@router.post("/client/api/demos/{demo_id}/end", response_model=DemoView)
def client_end_demo(
    demo_id: UUID, product: DashboardAuthedProduct, runner: Runner, log: Log
) -> DemoView:
    return end_demo(demo_id, product, runner, log)

_BLANK_CLIENT_GRAPH = """\
version: 1
site: client
base_url: https://example.com/
persona:
  product_name: your product
  one_liner: ""
  agent_name: Navigator AI
  tone: friendly, clear, concise
demo_playlist:
  - order: 1
    name: Default walkthrough
    page_id: home
    flow_id: default_walkthrough
pages:
  home:
    name: Home
    url: /
    selectors:
      body: body
    flows:
      default_walkthrough:
        - tool: wait_for
          selector: body
          timeout_ms: 15000
          expects: {check: visible, selector: body}
"""

@router.post("/client/api/bootstrap")
def client_bootstrap(request: Request, registry: Reg) -> dict:
    require_local_ops(request)

    """Ensure a client product + API key exist; persist key across reloads."""
    existing = resolve_client_api_key(settings.client_api_key)
    if existing:
        try:
            product = registry.authenticate(existing)
            settings.client_api_key = existing
            persist_client_key(existing)
            return {
                "ok": True,
                "product_id": product.product_id,
                "api_key": None,
                "message": "already configured",
            }
        except ProductNotFound:
            pass

    # One existing tenant, key lost (reload) → re-issue key, keep their graph/data.
    products = registry.list_products()
    if len(products) == 1:
        product = products[0]
        api_key = registry.rotate_api_key(product.product_id)
        settings.client_api_key = api_key
        persist_client_key(api_key)
        return {
            "ok": True,
            "product_id": product.product_id,
            "api_key": api_key,
            "message": "restored existing client; key saved to .navigator_client_key",
        }

    yaml_text = _BLANK_CLIENT_GRAPH
    try:
        parse_site_graph(yaml_text)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None

    spec = NewProduct(
        name="Your Product",
        product_id=f"client-{secrets.token_hex(3)}",
    )
    try:
        registered = registry.register(spec)
    except RegistryError as exc:
        raise HTTPException(409, str(exc)) from None

    registry.put_site_graph(
        registered.product.product_id, yaml_text, "yaml", publish=True
    )
    settings.client_api_key = registered.api_key
    persist_client_key(registered.api_key)
    return {
        "ok": True,
        "product_id": registered.product.product_id,
        "api_key": registered.api_key,
        "message": "key saved to .navigator_client_key",
    }

def _client_brief_id(registry: Registry, product: Product) -> str:
    """Stable id for bio/knowledge files = product_id (not mutable graph site)."""
    return product.product_id

@router.get("/client/api/product-login")
def client_get_product_login(product: DashboardAuthedProduct, vault: Vault) -> dict:
    """Public shape only — never the plaintext password."""
    return vault.public(product.product_id)

@router.put("/client/api/product-login")
def client_put_product_login(
    product: DashboardAuthedProduct, body: ProductLoginBody, vault: Vault
) -> dict:
    try:
        vault.put(
            product.product_id,
            login_url=body.login_url,
            username=body.username,
            password=body.password,
            include_login_in_default_flow=body.include_login_in_default_flow,
        )
    except VaultNotConfigured as exc:
        raise HTTPException(503, str(exc)) from None
    except CredentialVaultError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"ok": True, **vault.public(product.product_id)}

@router.delete("/client/api/product-login")
def client_delete_product_login(product: DashboardAuthedProduct, vault: Vault) -> dict:
    vault.delete(product.product_id)
    return {"ok": True, **vault.public(product.product_id)}

@router.get("/client/api/product-domain")
def client_get_product_domain(product: DashboardAuthedProduct, registry: Reg) -> dict:
    # Latest, not published: the dashboard edits drafts.
    try:
        graph = parse_site_graph(registry.latest_revision(product.product_id).yaml)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return {
        "base_url": graph.base_url,
        "placeholder": "example.com" in (graph.base_url or "").lower(),
    }

@router.get("/client/api/tier2")
def client_get_tier2(product: DashboardAuthedProduct, registry: Reg) -> dict:
    """Legacy: always off — product autonomy is guided-only."""
    _ = registry.get(product.product_id)
    return {"enabled": False}

@router.put("/client/api/tier2")
def client_put_tier2(
    product: DashboardAuthedProduct, body: Tier2Body, registry: Reg
) -> dict:
    """Legacy no-op — forces guided / Tier-2 off."""
    _ = body
    registry.set_tier2_enabled(product.product_id, False)
    return {"ok": True, "enabled": False}

@router.get("/client/api/autonomy-mode")
def client_get_autonomy_mode(product: DashboardAuthedProduct, registry: Reg) -> dict:
    fresh = registry.get(product.product_id)
    return {
        "mode": "guided",
        "tier2_enabled": False,
        "handoff_webhook_url": getattr(fresh, "handoff_webhook_url", "") or "",
    }

@router.put("/client/api/autonomy-mode")
def client_put_autonomy_mode(
    product: DashboardAuthedProduct, body: AutonomyModeBody, registry: Reg
) -> dict:
    """Legacy no-op — always guided."""
    _ = body
    updated = registry.set_autonomy_mode(product.product_id, "guided")
    return {
        "ok": True,
        "mode": "guided",
        "tier2_enabled": False,
    }

@router.put("/client/api/handoff-webhook")
def client_put_handoff_webhook(
    product: DashboardAuthedProduct, body: HandoffWebhookBody, registry: Reg
) -> dict:
    updated = registry.set_handoff_webhook(product.product_id, body.url)
    return {"ok": True, "url": updated.handoff_webhook_url}

@router.get("/client/api/agent-settings")
def client_get_agent_settings(
    product: DashboardAuthedProduct, registry: Reg, vault: Vault
) -> dict:
    settings_view = registry.get_agent_settings(product.product_id).model_dump()
    return {**settings_view, **vault.provider_keys_public(product.product_id)}

@router.put("/client/api/agent-settings")
def client_put_agent_settings(
    product: DashboardAuthedProduct, body: AgentSettingsBody, registry: Reg
) -> dict:
    patch = body.model_dump(exclude_none=True)
    if "default_language" in patch and patch["default_language"] not in {"en", "hi"}:
        raise HTTPException(422, "default_language must be en or hi")
    if "agent_gender" in patch and patch["agent_gender"] not in {"female", "male"}:
        raise HTTPException(422, "agent_gender must be female or male")
    merged = registry.set_agent_settings(product.product_id, patch)
    return {"ok": True, **merged.model_dump()}

@router.put("/client/api/agent-provider-keys")
def client_put_agent_provider_keys(
    product: DashboardAuthedProduct, body: AgentProviderKeysBody, vault: Vault
) -> dict:
    try:
        vault.put_provider_keys(
            product.product_id,
            gemini_api_key=body.gemini_api_key,
            groq_api_key=body.groq_api_key,
            openai_api_key=body.openai_api_key,
            anthropic_api_key=body.anthropic_api_key,
            openrouter_api_key=body.openrouter_api_key,
            huggingface_api_key=body.huggingface_api_key,
        )
    except VaultNotConfigured as exc:
        raise HTTPException(503, str(exc)) from None
    except CredentialVaultError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"ok": True, **vault.provider_keys_public(product.product_id)}

def _provider_models_for(
    product_id: str,
    provider: str,
    *,
    vault: Vault,
    api_key: str | None,
    base_url: str | None = None,
) -> dict:
    from navigator.client.provider_models import list_provider_models

    kind = provider.strip().lower()
    if kind not in {
        "gemini",
        "groq",
        "openai",
        "anthropic",
        "ollama",
        "vllm",
        "llamacpp",
        "openrouter",
        "huggingface",
    }:
        raise HTTPException(422, "unsupported provider")

    key = (api_key or "").strip()
    resolved_base_url = (base_url or "").strip()
    needs_vault_key = kind in {"gemini", "groq", "openai", "anthropic", "openrouter", "huggingface"}

    if needs_vault_key and not key:
        key = vault.provider_key(product_id, kind) or ""

    if kind in {"ollama", "vllm", "llamacpp"} and not resolved_base_url:
        from navigator.app.registry import Registry
        with Registry(settings.db_path) as reg:
            agent_settings = reg.get_agent_settings(product_id)
        if kind == "ollama":
            resolved_base_url = (agent_settings.ollama_base_url or "").strip()
        elif kind == "vllm":
            resolved_base_url = (agent_settings.vllm_base_url or "").strip()
        else:
            resolved_base_url = (agent_settings.llamacpp_base_url or "").strip()

    if kind in {"gemini", "groq", "openai", "anthropic", "openrouter", "huggingface"} and not key:
        raise HTTPException(400, f"No {kind} API key saved — connect or paste a key first")

    if kind in {"ollama", "vllm", "llamacpp"} and not resolved_base_url:
        raise HTTPException(400, f"No {kind} base URL saved — paste one in Settings first")

    try:
        models = list_provider_models(kind, key, base_url=resolved_base_url)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:
        raise HTTPException(502, f"Could not list {kind} models: {exc}") from None
    return {"ok": True, "provider": kind, "models": models}

@router.get("/client/api/agent-provider-models")
def client_get_agent_provider_models(
    product: DashboardAuthedProduct,
    vault: Vault,
    provider: str = Query(
        ...,
        pattern="^(gemini|groq|openai|anthropic|ollama|vllm|llamacpp|openrouter|huggingface)$",
    ),
) -> dict:
    return _provider_models_for(
        product.product_id, provider, vault=vault, api_key=None, base_url=None
    )

@router.post("/client/api/agent-provider-models")
def client_post_agent_provider_models(
    product: DashboardAuthedProduct, body: ProviderModelsBody, vault: Vault
) -> dict:
    return _provider_models_for(
        product.product_id,
        body.provider,
        vault=vault,
        api_key=body.api_key,
        base_url=body.base_url,
    )

_READY_CACHE: dict[tuple, tuple[float, dict]] = {}

_READY_CACHE_S = 12.0

def _cached_readiness_dict(
    registry: Registry,
    product_id: str,
    *,
    origin: str,
    autonomy_mode: str,
) -> dict:
    from navigator.agent.readiness import assess_demo_readiness

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return assess_demo_readiness(
            registry, product_id, origin=origin, autonomy_mode=autonomy_mode
        ).as_dict()
    key = (product_id, origin, autonomy_mode)
    now = time.monotonic()
    hit = _READY_CACHE.get(key)
    if hit and now - hit[0] < _READY_CACHE_S:
        return hit[1]
    payload = assess_demo_readiness(
        registry, product_id, origin=origin, autonomy_mode=autonomy_mode
    ).as_dict()
    _READY_CACHE[key] = (now, payload)
    return payload

@router.get("/client/api/demo-readiness")
def client_demo_readiness(
    product: DashboardAuthedProduct,
    registry: Reg,
    origin: Annotated[str, Query()] = "dashboard_test",
) -> dict:
    demo_origin = "public_embed" if origin == "public_embed" else "dashboard_test"
    return _cached_readiness_dict(
        registry,
        product.product_id,
        origin=demo_origin,
        autonomy_mode="guided",
    )

@router.get("/client/api/publish-checklist")
def client_publish_checklist(product: DashboardAuthedProduct, registry: Reg) -> dict:
    readiness = _cached_readiness_dict(
        registry, product.product_id, origin="public_embed", autonomy_mode="guided"
    )
    eval_score: float | None = None
    eval_path = Path(f"tests/eval/demo_brain/{product.product_id}.yaml")
    if eval_path.is_file():
        try:
            from navigator.agent.eval.runner import load_cases, run_eval
            from navigator.agent.nodes.planning import _flow_texts_for_page
            from navigator.knowledge.context import retrieve_context

            pub_rev = registry.published_revision(product.product_id)
            graph = registry.load_graph(product.product_id, pub_rev)
            page_id = next(iter(graph.pages), "")
            flow_texts = _flow_texts_for_page(
                type("D", (), {"graph": graph, "product_id": product.product_id})(),
                page_id,
            )
            report = run_eval(
                load_cases(eval_path),
                graph=graph,
                page_id=page_id,
                product_id=product.product_id,
                retrieve=retrieve_context,
                flow_texts=flow_texts,
            )
            eval_score = report.score_pct
        except Exception:  # noqa: BLE001
            eval_score = None
    return {
        "readiness": readiness,
        "eval_score_pct": eval_score,
        "autonomy_recommendation": "Demos use published flows and knowledge.",
    }

@router.put("/client/api/product-domain")
def client_put_product_domain(
    product: DashboardAuthedProduct, request: Request, body: ProductDomainBody, registry: Reg
) -> dict:
    if "example.com" in body.base_url.lower():
        raise HTTPException(422, "Please enter your actual product domain")
    from urllib.parse import urlparse
    parsed = urlparse(body.base_url)
    if not parsed.scheme:
        body.base_url = "https://" + body.base_url
        parsed = urlparse(body.base_url)
    normalized = f"{parsed.scheme}://{parsed.netloc}/"
    try:
        rev = registry.latest_revision(product.product_id)
        yaml_text = apply_base_url_to_yaml(rev.yaml, normalized)
        rev = registry.put_site_graph(
            product.product_id, yaml_text, "yaml", publish=False
        )
        graph = parse_site_graph(rev.yaml)
        settings.product_url = normalized
    except Exception as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "ok": True,
        "base_url": graph.base_url,
        "revision": rev.revision,
        "published": rev.published,
        "placeholder": "example.com" in (graph.base_url or "").lower(),
    }

@router.get("/client/api/site-graph")
def client_get_site_graph(product: DashboardAuthedProduct, registry: Reg) -> dict:
    """The revision the Client is editing -- the latest, draft or published."""
    try:
        rev = registry.latest_revision(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return {
        "yaml": rev.yaml,
        "revision": rev.revision,
        "site": rev.site,
        "published": rev.published,
        "published_revision": product.active_revision,
    }

@router.post("/client/api/site-graph/clear")
def client_clear_site_graph(product: DashboardAuthedProduct, registry: Reg) -> dict:
    """Reset draft site graph to empty shell + clear demo script metadata."""
    try:
        rev = registry.latest_revision(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    try:
        new_yaml = reset_site_graph_for_explore(rev.yaml)
        rev = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "yaml": new_yaml,
        "revision": rev.revision,
        "site": graph.site,
        "published": False,
        "playlist": playlist_from_graph(graph),
        "cleared": True,
    }

@router.put("/client/api/site-graph")
def client_put_site_graph(
    product: DashboardAuthedProduct, body: SiteGraphBody, registry: Reg, vault: Vault
) -> dict:
    """Save as a draft. End Users keep the published revision until publish."""
    try:
        _reject_login_in_yaml(product.product_id, body.yaml, vault)
        rev = registry.put_site_graph(
            product.product_id, body.yaml, "yaml", publish=False
        )
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "ok": True,
        "revision": rev.revision,
        "site": rev.site,
        "published": False,
    }

def _demo_script_inputs(
    product: DashboardAuthedProduct, registry: Reg, vault: Vault
) -> tuple[Any, Any, dict[str, str], str, bool, dict[str, Any]]:
    """Draft revision graph + bio/knowledge/login context for script composer."""
    rev = registry.latest_revision(product.product_id)
    graph = parse_site_graph(rev.yaml)
    bio_raw = load_bio(product.product_id)
    bio_fields: dict[str, str] = {}
    for f in bio_raw.get("fields") or []:
        if isinstance(f, dict) and f.get("key"):
            bio_fields[str(f["key"])] = str(f.get("value") or "")
    knowledge = load_product_brief(product.product_id)
    if not knowledge.strip():
        try:
            knowledge = load_product_brief(graph.site)
        except Exception:  # noqa: BLE001
            knowledge = ""
    include_login = vault.include_login_in_default_flow(product.product_id)
    stored = graph.demo_script_meta()
    return rev, graph, bio_fields, knowledge, include_login, stored

@router.get("/client/api/site-graph/demo-script")
def client_get_demo_script(
    product: DashboardAuthedProduct,
    registry: Reg,
    vault: Vault,
    flow_id: str | None = None,
) -> dict:
    """Compose full-demo script beats for the current draft revision."""
    try:
        rev, graph, bio_fields, knowledge, include_login, stored = _demo_script_inputs(
            product, registry, vault
        )
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    fid = (flow_id or "").strip() or None
    script = compose_full_demo_script(
        graph,
        product_id=product.product_id,
        knowledge_md=knowledge,
        bio_fields=bio_fields,
        include_login=include_login,
        stored_script=stored,
        flow_id_filter=fid,
    )
    script = merge_manual_overrides(script, stored)
    return {
        "revision": rev.revision,
        "published_revision": product.active_revision,
        "playlist": playlist_from_graph(graph),
        "flow_id": fid,
        **script,
    }

@router.patch("/client/api/site-graph/demo-script")
def client_patch_demo_script(
    product: DashboardAuthedProduct,
    body: DemoScriptPatchBody,
    registry: Reg,
    vault: Vault,
) -> dict:
    """Save demo script beat edits to draft `_meta.demo_script` (+ sync flow steps)."""
    try:
        rev, graph, bio_fields, knowledge, include_login, _stored = _demo_script_inputs(
            product, registry, vault
        )
        new_yaml = apply_script_patch(rev.yaml, beats=body.beats, sync_flow_steps=True)
        updated = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    script = compose_full_demo_script(
        graph,
        product_id=product.product_id,
        knowledge_md=knowledge,
        bio_fields=bio_fields,
        include_login=include_login,
        stored_script=graph.demo_script_meta(),
    )
    return {
        "ok": True,
        "revision": updated.revision,
        "published_revision": product.active_revision,
        "playlist": playlist_from_graph(graph),
        **script,
    }

@router.post("/client/api/site-graph/demo-script/regenerate")
def client_regenerate_demo_script(
    product: DashboardAuthedProduct,
    registry: Reg,
    vault: Vault,
    flow_id: str | None = None,
) -> dict:
    """Re-compose demo script; preserve beats with spoken_source=manual."""
    try:
        rev, graph, bio_fields, knowledge, include_login, stored = _demo_script_inputs(
            product, registry, vault
        )
        fid = (flow_id or "").strip() or None
        agent = registry.get_agent_settings(product.product_id)
        script = regenerate_demo_script(
            graph,
            product_id=product.product_id,
            knowledge_md=knowledge,
            bio_fields=bio_fields,
            include_login=include_login,
            stored_script=stored,
            flow_id_filter=fid,
            spoken_language=agent.default_language,
            agent_gender=agent.agent_gender,
        )
        new_yaml = apply_script_patch(
            rev.yaml, beats=script.get("beats") or [], sync_flow_steps=False
        )
        updated = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "ok": True,
        "revision": updated.revision,
        "published_revision": product.active_revision,
        "playlist": playlist_from_graph(graph),
        **script,
    }

@router.post("/client/api/site-graph/publish")
def client_publish_site_graph(
    product: DashboardAuthedProduct,
    registry: Reg,
    vault: Vault,
    revision: Annotated[int | None, Body(embed=True)] = None,
) -> dict:
    """Make a revision live for End Users. Defaults to the latest draft."""
    try:
        target = revision or registry.latest_revision(product.product_id).revision
        rev = registry.get_revision(product.product_id, target)
        _reject_login_in_yaml(product.product_id, rev.yaml, vault)
        updated = registry.activate(product.product_id, target)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    index_summary: dict | None = None
    try:
        graph = registry.load_graph(product.product_id, updated.active_revision)
        from navigator.knowledge.publish_index import index_on_publish

        index_summary = index_on_publish(
            product_id=product.product_id,
            graph=graph,
            revision=updated.active_revision,
            chroma_path=settings.chroma_path,
        ).as_dict()
    except Exception:  # noqa: BLE001
        index_summary = None
    return {
        "ok": True,
        "published_revision": updated.active_revision,
        "index": index_summary,
    }

@router.get("/client/api/bio")
def client_get_bio(product: DashboardAuthedProduct, registry: Reg) -> dict:
    data = load_bio(product.product_id)
    if not any(str(f.get("value") or "").strip() for f in data.get("fields", [])):
        try:
            site = registry.load_graph(product.product_id).site
            alt = load_bio(site)
            if any(str(f.get("value") or "").strip() for f in alt.get("fields", [])):
                data = alt
        except Exception:
            pass
    return data

@router.put("/client/api/bio")
def client_put_bio(product: DashboardAuthedProduct, body: BioBody, registry: Reg) -> dict:
    try:
        return save_bio(product.product_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

@router.get("/client/api/knowledge")
def client_get_knowledge(product: DashboardAuthedProduct, registry: Reg) -> dict:
    from navigator.knowledge.knowledge_merge import load_knowledge_bundle

    bundle = load_knowledge_bundle(product.product_id)
    if not str(bundle.get("markdown") or "").strip():
        try:
            site = registry.load_graph(product.product_id).site
            alt = load_knowledge_bundle(site)
            if str(alt.get("markdown") or "").strip():
                bundle = alt
        except Exception:  # noqa: BLE001
            pass
    return {
        "markdown": bundle.get("markdown") or "",
        "user_markdown": bundle.get("user_markdown") or "",
        "explore_markdown": bundle.get("explore_markdown") or "",
        "merged_at": bundle.get("merged_at"),
    }

@router.put("/client/api/knowledge")
def client_put_knowledge(product: DashboardAuthedProduct, body: KnowledgeBody, registry: Reg) -> dict:
    """Save canonical (editable merged) knowledge and re-index."""
    saved = save_product_brief(product.product_id, body.markdown)
    chroma_id = None
    if saved.strip():
        try:
            from navigator.knowledge.publish_index import index_knowledge_draft

            rev = registry.latest_revision(product.product_id).revision
            chroma_id = index_knowledge_draft(
                product_id=product.product_id,
                text=saved,
                revision=rev,
                chroma_path=settings.chroma_path,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[client] chroma ingest skipped: {exc}", flush=True)
    return {"markdown": saved, "chroma_id": chroma_id}

@router.put("/client/api/knowledge/user")
def client_put_knowledge_user(
    product: DashboardAuthedProduct, body: KnowledgeBody, registry: Reg
) -> dict:
    """Save Client-authored source MD, auto-merge into canonical, re-index."""
    from navigator.knowledge.knowledge_merge import (
        auto_merge_knowledge,
        save_user_markdown,
    )

    save_user_markdown(product.product_id, body.markdown)
    bundle = auto_merge_knowledge(product.product_id)
    chroma_id = None
    saved = str(bundle.get("markdown") or "")
    if saved.strip():
        try:
            from navigator.knowledge.publish_index import index_knowledge_draft

            rev = registry.latest_revision(product.product_id).revision
            chroma_id = index_knowledge_draft(
                product_id=product.product_id,
                text=saved,
                revision=rev,
                chroma_path=settings.chroma_path,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[client] chroma ingest skipped: {exc}", flush=True)
    return {
        "ok": True,
        "user_markdown": bundle.get("user_markdown") or "",
        "explore_markdown": bundle.get("explore_markdown") or "",
        "markdown": saved,
        "merged_at": bundle.get("merged_at"),
        "chroma_id": chroma_id,
    }

@router.get("/client/api/product-explore/topology")
def client_product_explore_topology(product: DashboardAuthedProduct) -> dict:
    from navigator.knowledge.topology import load_topology

    return load_topology(product.product_id)

@router.get("/client/api/product-explore")
def client_product_explore_status(product: DashboardAuthedProduct) -> dict:
    from navigator.automation.product_explore import active_job, status_dict

    job = active_job()
    if job is not None and job.product_id != product.product_id:
        # Another tenant's job — still return this product's disk artifacts.
        return {**status_dict(product_id=product.product_id), "active": False}
    return status_dict(product_id=product.product_id)

@router.post("/client/api/product-explore/start")
def client_product_explore_start(
    product: DashboardAuthedProduct,
    body: ProductExploreStartBody,
    registry: Reg,
    vault: Vault,
) -> dict:
    from navigator.automation.product_explore import start_job, status_dict
    from navigator.core.settings import settings as app_settings

    start_url = (body.start_url or "").strip()
    if not start_url:
        try:
            start_url = registry.load_graph(product.product_id).base_url or ""
        except Exception:  # noqa: BLE001
            start_url = ""
    if not start_url:
        try:
            creds = vault.credentials_for(product.product_id)
            if creds:
                start_url = str(creds[0] or "").strip()
        except Exception:  # noqa: BLE001
            start_url = ""
    if not start_url:
        raise HTTPException(422, "start_url required (or set product domain / Product Login URL)")

    def _login_cfg() -> Any:
        try:
            creds = vault.credentials_for(product.product_id)
            if not creds:
                return None
            login_url, username, password = creds
            return type(
                "Creds",
                (),
                {
                    "login_url": login_url,
                    "username": username,
                    "password": password,
                },
            )()
        except Exception:  # noqa: BLE001
            return None

    try:
        start_job(
            product_id=product.product_id,
            start_url=start_url,
            login_config_fn=_login_cfg,
            browser_ws=(app_settings.record_browser_ws or "").strip(),
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    return status_dict(product_id=product.product_id)

@router.post("/client/api/product-explore/stop")
def client_product_explore_stop(product: DashboardAuthedProduct) -> dict:
    from navigator.automation.product_explore import active_job, stop_job

    job = active_job()
    if job is None:
        raise HTTPException(409, "no active product explore")
    if job.product_id != product.product_id:
        raise HTTPException(409, "no active product explore for this product")
    return stop_job()


@router.post("/client/api/product-explore/ack")
def client_product_explore_ack(product: DashboardAuthedProduct) -> dict:
    """Dismiss a finished explore job (clears in-memory done state)."""
    from navigator.automation.product_explore import ack_job

    try:
        return ack_job(product_id=product.product_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/client/api/flows")
def client_get_flows(product: DashboardAuthedProduct, registry: Reg) -> dict:
    try:
        graph = parse_site_graph(registry.latest_revision(product.product_id).yaml)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return {"playlist": playlist_from_graph(graph), "site": graph.site}

@router.put("/client/api/flows")
def client_put_flows(product: DashboardAuthedProduct, body: FlowsBody, registry: Reg) -> dict:
    try:
        rev = registry.latest_revision(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    try:
        new_yaml = apply_playlist_to_yaml(rev.yaml, body.playlist)
        rev = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "playlist": playlist_from_graph(graph),
        "revision": rev.revision,
        "published": False,
    }

@router.post("/client/api/flows/clear")
def client_clear_all_flows(product: DashboardAuthedProduct, registry: Reg) -> dict:
    """Reset flows, demo script, and explored pages — fresh shell for Auto-Explore."""
    try:
        rev = registry.latest_revision(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    try:
        new_yaml = reset_site_graph_for_explore(rev.yaml)
        rev = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "yaml": new_yaml,
        "playlist": playlist_from_graph(graph),
        "revision": rev.revision,
        "published": False,
        "cleared": True,
    }

@router.post("/client/api/flows/delete")
def client_delete_flow(
    product: DashboardAuthedProduct, body: FlowDeleteBody, registry: Reg
) -> dict:
    """Remove a flow from the playlist and site-graph draft (unpublished)."""
    try:
        rev = registry.latest_revision(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    try:
        new_yaml = remove_flow_from_yaml(
            rev.yaml, flow_id=body.flow_id, page_id=body.page_id
        )
        rev = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "playlist": playlist_from_graph(graph),
        "revision": rev.revision,
        "published": False,
        "deleted_flow_id": body.flow_id.strip(),
    }

@router.patch("/client/api/flows/semantics")
def client_patch_flow_semantics(
    product: DashboardAuthedProduct, body: FlowSemanticsBody, registry: Reg
) -> dict:
    """Client edits generated purpose / tags / name under `_meta.semantics`."""
    import yaml as _yaml

    try:
        rev = registry.latest_revision(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    raw = _yaml.safe_load(rev.yaml)
    if not isinstance(raw, dict):
        raise HTTPException(422, "site graph must be a mapping")
    meta = raw.setdefault("_meta", {})
    if not isinstance(meta, dict):
        raise HTTPException(422, "_meta must be a mapping")
    bucket = meta.setdefault("semantics", {})
    if not isinstance(bucket, dict):
        raise HTTPException(422, "_meta.semantics must be a mapping")
    fid = body.flow_id.strip()
    entry = bucket.get(fid) if isinstance(bucket.get(fid), dict) else {}
    entry = dict(entry)
    if body.purpose is not None:
        entry["purpose"] = body.purpose.strip()
    if body.tags is not None:
        entry["tags"] = [t.strip() for t in body.tags if t and t.strip()]
    if body.triggers is not None:
        entry["triggers"] = [t.strip() for t in body.triggers if t and t.strip()]
    if body.auto_name is not None:
        entry["auto_name"] = body.auto_name.strip()
    bucket[fid] = entry
    new_yaml = _yaml.safe_dump(raw, sort_keys=False)
    try:
        rev = registry.put_site_graph(
            product.product_id, new_yaml, "yaml", publish=False
        )
        graph = parse_site_graph(new_yaml)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "playlist": playlist_from_graph(graph),
        "revision": rev.revision,
        "published": False,
        "flow_id": fid,
        "semantics": entry,
    }

@router.get("/client/api/record")
def client_record_status(product: DashboardAuthedProduct) -> dict:
    return recorder_status()

@router.post("/client/api/record/start")
def client_record_start(
    product: DashboardAuthedProduct,
    body: RecordStartBody,
    vault: Vault,
    request: Request,
    registry: Reg,
) -> dict:
    from navigator.automation.login_match import LoginConfig
    from navigator.automation.record_ws import resolve_record_browser_ws
    from navigator.client.content import guided_task_meta

    def _live_login_config() -> LoginConfig:
        return LoginConfig(login_url=vault.login_url(product.product_id))

    mode = body.save_mode.strip().lower()
    if mode == "update":
        fid = (body.target_flow_id or body.flow_id or "").strip()
        if not fid:
            raise HTTPException(
                422, "target_flow_id required when save_mode is update"
            ) from None
        fname = (body.target_flow_name or body.flow_name).strip()
    else:
        fid = (body.flow_id or None)
        fname = body.flow_name.strip()

    peer = request.client.host if request.client else None
    try:
        browser_ws = resolve_record_browser_ws(
            configured=settings.record_browser_ws,
            path_token=settings.record_ws_path,
            peer_ip=peer,
            record_local=settings.record_local,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    # Guided plan must be on the gate before the WS worker starts.
    plan_meta: dict | None = None
    try:
        rev = registry.latest_revision(product.product_id)
        plan_meta = guided_task_meta(rev.yaml) or None
    except ProductNotFound:
        plan_meta = None

    try:
        job = start_recorder(
            start_url=body.start_url.strip(),
            flow_name=fname,
            flow_id=fid,
            headful=settings.headful,
            login_config_fn=_live_login_config,
            narrate=body.narrate,
            save_mode=mode,
            browser_ws=browser_ws,
            guided_plan_meta=plan_meta,
            product_id=product.product_id,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None

    return {
        "job_id": job.job_id,
        "flow_id": job.flow_id,
        "flow_name": job.flow_name,
        "active": True,
        "phase": job.phase,
        "narrate": bool(job.narration is not None),
        "save_mode": job.save_mode,
        "browser": "local" if browser_ws else "server",
    }

@router.post("/client/api/record/capture")
def client_record_capture(product: DashboardAuthedProduct) -> dict:
    """Leave Setup: from now on, clicks become flow content."""
    try:
        job = begin_capture()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    return {
        "ok": True,
        "phase": job.phase,
        "setup_discarded": job.setup_discarded
        if job.gate is None
        else job.gate.setup_discarded,
        "steps": len(job.steps),
    }

@router.post("/client/api/record/stop")
def client_record_stop(
    product: DashboardAuthedProduct,
    registry: Reg,
    vault: Vault,
    page_id: Annotated[str, Query()] = "dashboard",
) -> dict:
    from navigator.client.content import persist_recorder_job

    try:
        job = stop_recorder()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    if not getattr(job, "product_id", ""):
        job.product_id = product.product_id
    return persist_recorder_job(job, page_id=page_id or "dashboard")

_GUIDED_RETIRED = "Guided task retired — use manual record."

def _raise_guided_retired(product: DashboardAuthedProduct) -> None:
    raise HTTPException(410, _GUIDED_RETIRED) from None

@router.post("/client/api/guided-task/plan")
def client_guided_task_plan(product: DashboardAuthedProduct) -> None:
    _raise_guided_retired(product)

@router.get("/client/api/guided-task/status")
def client_guided_task_status(product: DashboardAuthedProduct) -> None:
    _raise_guided_retired(product)

@router.post("/client/api/guided-task/hands/start")
def client_guided_hands_start(product: DashboardAuthedProduct) -> None:
    _raise_guided_retired(product)

@router.post("/client/api/guided-task/hands/tick")
def client_guided_hands_tick(product: DashboardAuthedProduct) -> None:
    _raise_guided_retired(product)

@router.post("/client/api/guided-task/hands/stop")
def client_guided_hands_stop(product: DashboardAuthedProduct) -> None:
    _raise_guided_retired(product)

@router.post("/client/api/guided-task/hands/answer")
def client_guided_hands_answer(product: DashboardAuthedProduct) -> None:
    _raise_guided_retired(product)

@router.post("/client/api/guided-task/hands/pause")
def client_guided_hands_pause(product: DashboardAuthedProduct) -> None:
    _raise_guided_retired(product)

@router.post("/client/api/guided-task/hands/resume")
def client_guided_hands_resume(product: DashboardAuthedProduct) -> None:
    _raise_guided_retired(product)

@router.post("/client/api/guided-task/hands/barge")
def client_guided_hands_barge(product: DashboardAuthedProduct) -> None:
    _raise_guided_retired(product)

@router.patch("/client/api/guided-task/plan")
def client_guided_task_patch(product: DashboardAuthedProduct) -> None:
    _raise_guided_retired(product)

def _attach_recorded_narration(
    yaml_text: str,
    job,
    *,
    update_existing: bool = False,
    replace_steps: bool = False,
    prior_steps: int = 0,
) -> tuple[str, int]:
    """Transcribe the host's walkthrough onto the flow they just recorded.

    Always writes step_clicks + timing + placeholder narration so timeline
    playback can run even when STT fails. STT overwrites lines when it succeeds.
    """
    narration = getattr(job, "narration", None)
    if narration is None or not job.steps:
        return yaml_text, 0

    from navigator.automation.explore.runner import (
        _append_narration,
        _append_semantics,
        _attach_meta,
        groq_asker,
    )
    from navigator.automation.narration import (
        compact_timeline,
        narrate_recording,
        placeholder_narration_lines,
        rebuild_flow_narration,
        speech_windows_payload,
        step_timings,
    )
    from navigator.automation.record_scrub import (
        scrub_recorded_steps,
        step_mouse_paths_payload,
    )
    from navigator.core.groq_keys import groq_key_candidates

    scrubbed_steps = scrub_recorded_steps(list(job.steps))
    if not scrubbed_steps:
        return yaml_text, 0

    mouse_paths = step_mouse_paths_payload(scrubbed_steps)
    step_times = [int(getattr(s, "at_ms", 0) or 0) for s in scrubbed_steps]
    lines = placeholder_narration_lines(scrubbed_steps)
    speech: list[dict[str, int]] = []
    used_stt = False
    hints = [
        str(getattr(s, "alias", "") or "").replace("_", " ").strip()
        for s in scrubbed_steps
    ]

    audio = narration.audio()
    keys = groq_key_candidates()
    if audio and keys:
        api_key = keys[0]
        try:
            stt_lines, _stt_timings, _stt_windows = narrate_recording(
                audio=audio,
                steps=scrubbed_steps,
                api_key=api_key,
                ask_text=groq_asker(api_key),
                language=getattr(narration, "language", "auto") or "auto",
                translate_to=getattr(narration, "translate_to", "same") or "same",
            )
            if any(str(l).strip() for l in stt_lines):
                lines = stt_lines
                used_stt = True
        except Exception as exc:  # noqa: BLE001
            print(f"[record] narration STT failed (using placeholders): {exc}", flush=True)
    elif audio and not keys:
        print("[record] narration STT skipped: no Groq API keys", flush=True)

    if not used_stt:
        lines, timings, windows, click_ms = rebuild_flow_narration(
            lines=lines,
            step_times_ms=step_times,
            hints=hints,
            ask_text=None,
        )
    else:
        click_ms, windows = compact_timeline(lines)
        timings = step_timings(click_ms, lines)
    speech = speech_windows_payload(windows)
    clicks = [{"idx": i, "at_ms": int(t)} for i, t in enumerate(click_ms)]

    semantics_payload = {
        "purpose": "",
        "auto_name": job.flow_name,
        "steps": [
            {"idx": i, "description": line}
            for i, line in enumerate(lines)
            if line.strip()
        ],
    }

    if update_existing and not replace_steps:
        yaml_text = _append_narration(yaml_text, job.flow_id, lines)
        yaml_text = _append_semantics(
            yaml_text, job.flow_id, semantics_payload, offset=prior_steps
        )
        for section, rows in (
            ("step_timing", timings),
            ("step_clicks", clicks),
            ("step_speech", speech),
            ("step_mouse_paths", mouse_paths),
        ):
            if rows:
                yaml_text = _append_meta_rows(
                    yaml_text, section, job.flow_id, rows, offset=prior_steps
                )
    else:
        yaml_text = _attach_meta(
            yaml_text, "narration_suggestions", job.flow_id, lines
        )
        yaml_text = _attach_meta(
            yaml_text, "semantics", job.flow_id, semantics_payload
        )
        for section, rows in (
            ("step_timing", timings),
            ("step_clicks", clicks),
            ("step_speech", speech),
            ("step_mouse_paths", mouse_paths),
        ):
            if rows:
                yaml_text = _attach_meta(yaml_text, section, job.flow_id, rows)
    narrated = sum(1 for l in lines if str(l).strip())
    print(
        f"[record] narration: {narrated}/{len(lines)} steps have spoken lines",
        flush=True,
    )
    return yaml_text, narrated

def _append_meta_rows(
    yaml_text: str,
    section: str,
    flow_id: str,
    new_rows: list[dict],
    *,
    offset: int = 0,
) -> str:
    """Update mode: keep prior `idx` rows for this flow, append new ones shifted."""
    import yaml as _yaml

    from navigator.automation.explore.runner import _attach_meta

    raw = _yaml.safe_load(yaml_text)
    prior: list[dict] = []
    if isinstance(raw, dict):
        bucket = (raw.get("_meta") or {}).get(section) or {}
        if isinstance(bucket, dict) and isinstance(bucket.get(flow_id), list):
            prior = [r for r in bucket[flow_id] if isinstance(r, dict)]
    shifted = [
        {**row, "idx": int(row["idx"]) + offset}
        for row in new_rows
        if isinstance(row, dict)
    ]
    return _attach_meta(yaml_text, section, flow_id, prior + shifted)

_EXPLORE_RETIRED = (
    "Demo auto-explore retired — use Product Explore from Knowledge."
)

def _raise_explore_retired(product: DashboardAuthedProduct) -> None:
    raise HTTPException(410, _EXPLORE_RETIRED) from None

@router.get("/client/api/explore")
def client_explore_status(product: DashboardAuthedProduct, vault: Vault) -> dict:
    from navigator.automation.explore.runner import active_session

    session = active_session()
    status: dict = (
        {"active": False}
        if session is None or session.product_id != product.product_id
        else session.status()
    )
    # public(), not credentials_for(): the dashboard only needs to know a login
    # exists, and this route must not decrypt (or fail on an unconfigured key).
    status["has_credentials"] = bool(vault.public(product.product_id)["has_password"])
    return status

@router.get("/client/api/explore/frame")
def client_explore_frame(product: DashboardAuthedProduct) -> dict:
    """Latest JPEG viewport from the server Chromium running this explore."""
    from navigator.automation.explore.runner import active_session

    session = active_session()
    if session is None or session.product_id != product.product_id:
        raise HTTPException(409, "no active exploration")
    frame = session.frame_payload()
    if frame is None:
        raise HTTPException(404, "no frame yet")
    return frame

@router.post("/client/api/explore/start")
def client_explore_start(product: DashboardAuthedProduct) -> None:
    _raise_explore_retired(product)

@router.post("/client/api/explore/stop")
def client_explore_stop(product: DashboardAuthedProduct) -> None:
    _raise_explore_retired(product)

@router.post("/client/api/explore/answer")
def client_explore_answer(product: DashboardAuthedProduct) -> None:
    _raise_explore_retired(product)

@router.post("/client/api/explore/flagged")
def client_explore_flagged(product: DashboardAuthedProduct) -> None:
    _raise_explore_retired(product)

@router.post("/client/api/explore/ticket", status_code=201)
def client_explore_ticket(product: DashboardAuthedProduct) -> dict:
    """Mint a single-use, short-lived ticket for the exploration WebSocket.

    The browser WebSocket API cannot set an Authorization header, so the JWT is
    exchanged here (on an authed route) for a one-shot ticket carried in the
    query string. Same shape as the public `sess_` token: single use, hashed
    nowhere it can leak, and useless once redeemed.
    """
    from navigator.automation.explore.tickets import mint_ticket

    return {"ticket": mint_ticket(product.product_id), "expires_in_s": 60}

@router.websocket("/client/api/explore/ws")
async def client_explore_ws(websocket: WebSocket, ticket: str = Query(default="")) -> None:
    """Live exploration channel: replayed log, then events as they happen.

    Read-only. Answers go over the authed POST route, not this socket, so a
    redeemed ticket can never be used to inject a field value.
    """
    import asyncio

    from navigator.automation.explore.runner import active_session
    from navigator.automation.explore.tickets import redeem_ticket

    product_id = redeem_ticket(ticket)
    if product_id is None:
        await websocket.close(code=4401)
        return
    session = active_session()
    if session is None or session.product_id != product_id:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    class _Bridge:
        """The explorer runs on a plain thread; hop each event onto the loop."""

        def put_nowait(self, event: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

    bridge = _Bridge()
    replay = session.add_listener(bridge)
    try:
        await websocket.send_json({"type": "status", **session.status()})
        for event in replay:
            await websocket.send_json(event)
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("type") == "done":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        session.remove_listener(bridge)

@router.get("/client/api/metrics")
def client_metrics(
    product: DashboardAuthedProduct,
    runner: Runner,
    log: Log,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> dict:
    """KPIs for the console. Durable counters from the action log, live state
    from the in-process runner (which is empty after a restart)."""
    metrics = log.dashboard_metrics(product.product_id, days=days)
    in_memory = runner.list(product.product_id)
    im_running = sum(1 for d in in_memory if d.status in ("starting", "running"))
    im_live_running = sum(
        1
        for d in in_memory
        if d.status in ("starting", "running") and d.origin == "public_embed"
    )
    im_test_running = sum(
        1
        for d in in_memory
        if d.status in ("starting", "running") and d.origin == "dashboard_test"
    )
    metrics["demos"]["running"] = max(metrics["demos"]["running"], im_running)
    metrics["live"]["running"] = max(metrics["live"]["running"], im_live_running)
    metrics["test"]["running"] = max(metrics["test"]["running"], im_test_running)
    metrics["run_series"] = metrics["series"]
    return metrics

@router.get("/client/api/runs", response_model=list[DemoRunView])
def client_list_runs(
    product: DashboardAuthedProduct,
    log: Log,
    runner: Runner,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> list[DemoRunView]:
    """Last N days of demo runs; reconcile stale running rows with the live runner."""
    from navigator.logs.store import utcnow

    live = {
        str(h.demo_id): h.status
        for h in runner.list(product.product_id)
        if h.status in ("starting", "running")
    }
    out: list[DemoRunView] = []
    for row in log.list_runs(product.product_id, days=days):
        did = str(row["demo_id"])
        status = row["status"]
        if did in live:
            status = live[did]
        elif status in ("starting", "running"):
            # Ghost row after restart / crash — close it in SQLite.
            log.update_run_status(
                UUID(row["session_id"]), "finished", ended_at=utcnow()
            )
            status = "finished"
        out.append(DemoRunView(**{**row, "status": status}))
    return out

@router.get("/client/api/runs/{session_id}", response_model=DemoRunView)
def client_get_run(
    session_id: UUID, product: DashboardAuthedProduct, log: Log
) -> DemoRunView:
    row = log.get_run(session_id, product.product_id)
    if row is None:
        raise HTTPException(404, "no such run")
    return DemoRunView(**row)

@router.get("/client/api/runs/{session_id}/events", response_model=list[ActionLogEntry])
def client_run_events(
    session_id: UUID, product: DashboardAuthedProduct, log: Log
) -> list[ActionLogEntry]:
    """Full ActionLog for one run — client dashboard only, never spoken."""
    row = log.get_run(session_id, product.product_id)
    entries = log.entries(session_id, product_id=product.product_id)
    if row is None and not entries:
        raise HTTPException(404, "no such run")
    return entries

@router.get("/client/api/runs/{session_id}/decisions", response_model=list[DecisionTraceView])
def client_run_decisions(
    session_id: UUID, product: DashboardAuthedProduct, log: Log
) -> list[DecisionTraceView]:
    from navigator.logs.decisions import DecisionTraceStore

    with DecisionTraceStore(settings.db_path) as store:
        rows = store.for_session(session_id, product_id=product.product_id)
    if not rows:
        row = log.get_run(session_id, product.product_id)
        if row is None:
            raise HTTPException(404, "no such run")
    return [
        DecisionTraceView(
            id=r.id,
            session_id=r.session_id,
            utterance=r.utterance,
            branch=r.branch,
            chosen_flow_id=r.chosen_flow_id,
            spoken=r.spoken,
            flow_candidates=[[f, c] for f, c in r.flow_candidates],
            knowledge_hits=[[k, s] for k, s in r.knowledge_hits],
            detail=r.detail,
            created_at=r.created_at,
        )
        for r in rows
    ]
