"""Shared helpers used by more than one API router."""

from __future__ import annotations

from typing import Callable

from fastapi import HTTPException

from navigator.app.api_models import (
    IntakePrefill,
    LiveDemoView,
    MeetingOut,
    StartLiveDemo,
)
from navigator.app.credential_vault import CredentialVault
from navigator.app.registry import Product, ProductNotFound, Registry
from navigator.app.runner import DemoOrigin, DemoRunner
from navigator.core.settings import settings
from navigator.knowledge.site_graph import SiteGraphError, parse_site_graph
from navigator.meeting.providers import (
    MeetingProvider,
    MeetingProviderError,
    Platform as MeetingPlatform,
)

def _reject_login_in_yaml(
    product_id: str,
    yaml_text: str,
    vault: CredentialVault,
    *,
    allow_flows: frozenset[tuple[str, str]] | None = None,
) -> None:
    """Save/activate gate: login steps belong in Product Login, not in flows."""
    from navigator.automation.login_match import LoginConfig, assert_no_login_in_graph

    graph = parse_site_graph(yaml_text, origin=f"product {product_id}")
    assert_no_login_in_graph(
        graph,
        LoginConfig(login_url=vault.login_url(product_id)),
        allow_flows=allow_flows,
    )

def _run_live_demo(
    spec: StartLiveDemo,
    product: Product,
    token_intake: IntakePrefill | None,
    registry: Registry,
    runner: DemoRunner,
    providers: Callable[[MeetingPlatform | None], MeetingProvider],
    *,
    origin: DemoOrigin,
) -> LiveDemoView:
    """Create a meeting for *this* session and put Navigator in it, now.

    The meeting is instant, not scheduled: it exists and is joinable the moment
    this returns, which is the only useful semantics for a "Show Demo" button.
    The link is minted per call, so two prospects can be demoed at once and no
    human has to set NAVIGATOR_MEETING_URL first. The response carries the join
    URL, which is what the button redirects to.

    Ordering is deliberate: the graph is loaded and the flow validated *before*
    any meeting is created, so a bad request never leaves an orphaned meeting
    behind.

    Revision resolution is the boundary that matters here. A live demo runs the
    published revision and nothing else, so an End User can never be shown a
    half-finished draft the Client is still editing. A dashboard test demo runs
    the Client's latest revision, draft included -- validating a draft before
    publishing is the entire point of a test demo.
    """
    try:
        if origin == "public_embed":
            revision = registry.published_revision(product.product_id)
        else:
            revision = registry.latest_revision(product.product_id).revision
        graph = registry.load_graph(product.product_id, revision)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None

    try:
        rev_yaml = registry.get_revision(product.product_id, revision).yaml
        from navigator.agent.readiness import assert_live_graph_yaml, assess_demo_readiness

        assert_live_graph_yaml(rev_yaml)
        readiness = assess_demo_readiness(
            registry,
            product.product_id,
            origin=origin,
            autonomy_mode="guided",
        )
        blocking = [c for c in readiness.checks if c.blocking and not c.ok]
        if blocking:
            raise HTTPException(422, f"Demo not ready: {blocking[0].message}")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    page_id = spec.page_id
    flow_id = spec.flow_id
    if spec.auto_play and graph.demo_playlist:
        primary = graph.primary_flow()
        if primary:
            page_id, flow_id = primary
    elif not page_id or not flow_id:
        primary = graph.primary_flow()
        if primary:
            page_id = page_id or primary[0]
            flow_id = flow_id or primary[1]
    page_id = page_id or next(iter(graph.pages), "")
    if not flow_id:
        flow_id = settings.live_walkthrough_flow
    try:
        graph.flow(page_id, flow_id)
    except SiteGraphError as exc:
        raise HTTPException(422, str(exc)) from None

    try:
        topic = spec.topic or f"Navigator demo — {product.name}"
        meeting = providers(spec.platform).create_meeting(
            product.product_id, topic=topic
        )
    except MeetingProviderError as exc:
        # 502: the request was fine, the upstream conferencing provider was not.
        raise HTTPException(502, f"could not create meeting: {exc}") from None

    if not meeting.open_access:
        if origin == "public_embed":
            # End Users cannot admit a bot from a waiting room.
            raise HTTPException(
                422,
                "This meeting link is not open-access: Navigator would wait in the "
                "lobby for a host that never comes. Use platform 'google_meet' "
                "(creates a new open Meet space) or 'zoom' (Navigator joins as "
                "Zoom host via ZAK).",
            )
        # Dashboard test + static: Client is the host. Navigator joins as guest;
        # Client opens the link and admits the bot (admit-flow, not bot-first).
        print(
            f"[api] dashboard_test static link {meeting.url} — "
            "admit-flow (you are host; admit Navigator when Meet asks)",
            flush=True,
        )

    live_kw: dict = {
        "intake_prefill": (
            (token_intake or spec.intake).model_dump()
            if (token_intake or spec.intake)
            else None
        ),
        "auto_play": bool(spec.auto_play),
    }
    if not meeting.open_access and origin == "dashboard_test":
        live_kw["bot_first"] = False
        live_kw["open_meet_in_browser"] = True

    handle = runner.start_live(
        product.product_id,
        graph,
        revision,
        (page_id, flow_id),
        meeting_url=meeting.url,
        platform=meeting.platform,
        origin=origin,
        **live_kw,
    )
    return LiveDemoView(**handle.public(), meeting=MeetingOut(**meeting.public()))

def apply_base_url_to_yaml(yaml_text: str, base_url: str) -> str:
    import yaml
    from copy import deepcopy
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        raise ValueError("invalid site graph yaml")
    data["base_url"] = base_url
    return yaml.dump(data, sort_keys=False, default_flow_style=False)
