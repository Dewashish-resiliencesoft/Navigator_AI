"""Pre-flight demo readiness checks for Client dashboard."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from navigator.app.registry import ProductNotFound, Registry
from navigator.automation.explore.validate import is_offerable
from navigator.core.settings import settings
from navigator.knowledge.site_graph import SiteGraph, SiteGraphError

AutonomyMode = Literal["guided", "adaptive", "explorer"]


@dataclass(frozen=True)
class ReadinessCheck:
    id: str
    ok: bool
    message: str
    blocking: bool = False


@dataclass(frozen=True)
class DemoReadiness:
    score: int
    checks: tuple[ReadinessCheck, ...]
    autonomy_mode: str

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "autonomy_mode": self.autonomy_mode,
            "checks": [
                {
                    "id": c.id,
                    "ok": c.ok,
                    "message": c.message,
                    "blocking": c.blocking,
                }
                for c in self.checks
            ],
        }


def _has_offerable_flow(graph: SiteGraph) -> bool:
    for page_id, page in graph.pages.items():
        for fid in page.flows:
            if is_offerable(graph.flow_validation(fid)):
                try:
                    if list(graph.flow(page_id, fid)):
                        return True
                except SiteGraphError:
                    continue
    if graph.demo_playlist:
        for item in graph.demo_playlist:
            try:
                if is_offerable(graph.flow_validation(item.flow_id)):
                    if list(graph.flow(item.page_id, item.flow_id)):
                        return True
            except SiteGraphError:
                continue
    return False


def _flow_has_semantics_or_triggers(graph: SiteGraph) -> bool:
    for page in graph.pages.values():
        for fid in page.flows:
            sem = graph.flow_semantics(fid)
            if sem.get("purpose") or sem.get("tags") or sem.get("triggers"):
                return True
    return False


def _knowledge_count(product_id: str, chroma_path: Path) -> int:
    try:
        from navigator.knowledge.memory.collections import get_collection

        coll = get_collection(chroma_path, product_id, "product_knowledge")
        return int(coll.count())
    except Exception:  # noqa: BLE001
        return 0


def _draft_graph_if_ahead(
    registry: Registry, product_id: str, published_rev: int | None
) -> tuple[SiteGraph | None, int | None]:
    """Draft revision when it is newer than what visitors see."""
    if published_rev is None:
        return None, None
    try:
        draft_rev = registry.latest_revision(product_id)
    except ProductNotFound:
        return None, None
    if draft_rev.revision <= published_rev:
        return None, None
    try:
        return registry.load_graph(product_id, draft_rev.revision), draft_rev.revision
    except (ProductNotFound, SiteGraphError):
        return None, None


def assert_live_graph_yaml(yaml_text: str) -> None:
    """Reject fixture graphs for live demos (API path)."""
    if "tests/fixtures" in yaml_text or "crm_dashboard.html" in yaml_text:
        raise ValueError(
            "Site graph points at a test fixture, not a live product. "
            "Record your product or upload a real site graph before starting a demo."
        )


def _attendee_ok() -> bool:
    from navigator.meeting.attendee_stack import attendee_reachable

    local = any(h in settings.attendee_base_url for h in ("localhost", "127.0.0.1"))
    if not local:
        return True
    return attendee_reachable(settings.attendee_base_url)


def assess_demo_readiness(
    registry: Registry,
    product_id: str,
    *,
    origin: Literal["dashboard_test", "public_embed"] = "dashboard_test",
    autonomy_mode: AutonomyMode = "guided",
    chroma_path: Path | None = None,
) -> DemoReadiness:
    """Score 0–100; blocking checks gate live demo start."""
    chroma_path = chroma_path or settings.chroma_path
    checks: list[ReadinessCheck] = []

    try:
        product = registry.get(product_id)
    except ProductNotFound:
        return DemoReadiness(
            score=0,
            checks=(
                ReadinessCheck(
                    id="product",
                    ok=False,
                    message="Product not found.",
                    blocking=True,
                ),
            ),
            autonomy_mode=autonomy_mode,
        )

    autonomy_mode = getattr(product, "autonomy_mode", None) or autonomy_mode

    if origin == "public_embed" and autonomy_mode == "explorer":
        checks.append(
            ReadinessCheck(
                id="explorer_embed",
                ok=False,
                message="Explorer mode is not allowed on the public embed.",
                blocking=True,
            )
        )

    published_rev: int | None = None
    try:
        published_rev = registry.published_revision(product_id)
        checks.append(
            ReadinessCheck(
                id="published",
                ok=True,
                message=f"Published revision {published_rev} is live.",
                blocking=origin == "public_embed",
            )
        )
    except ProductNotFound:
        checks.append(
            ReadinessCheck(
                id="published",
                ok=False,
                message="No published site graph — publish before live visitors can demo.",
                blocking=origin == "public_embed",
            )
        )

    gemini_ok = bool(settings.gemini_api_key)
    checks.append(
        ReadinessCheck(
            id="live",
            ok=gemini_ok,
            message=(
                "Gemini key set — Live audio can start."
                if gemini_ok
                else "Set NAVIGATOR_GEMINI_API_KEY — Meet voice is Gemini Live only."
            ),
            blocking=True,
        )
    )

    try:
        if origin == "public_embed" and published_rev is not None:
            graph = registry.load_graph(product_id, published_rev)
        else:
            # Test demos run latest draft — readiness must match what start will use.
            graph = registry.load_graph(
                product_id, registry.latest_revision(product_id).revision
            )
    except (ProductNotFound, SiteGraphError) as exc:
        checks.append(
            ReadinessCheck(
                id="graph",
                ok=False,
                message=f"Site graph invalid: {exc}",
                blocking=True,
            )
        )
        return DemoReadiness(
            score=_score(checks),
            checks=tuple(checks),
            autonomy_mode=autonomy_mode,
        )

    base_url = (graph.base_url or "").lower()
    if "fixtures" in base_url or base_url.endswith(".html"):
        checks.append(
            ReadinessCheck(
                id="live_url",
                ok=False,
                message="Site graph points at a fixture URL — set your product domain.",
                blocking=True,
            )
        )
    else:
        checks.append(
            ReadinessCheck(
                id="live_url",
                ok=True,
                message="Product URL looks live.",
            )
        )

    offerable = _has_offerable_flow(graph)
    draft_graph, draft_rev = (
        _draft_graph_if_ahead(registry, product_id, published_rev)
        if origin == "public_embed"
        else (None, None)
    )
    if offerable:
        offerable_msg = "At least one validated flow is ready to show."
    elif draft_graph and _has_offerable_flow(draft_graph) and draft_rev is not None:
        offerable_msg = (
            f"Published revision has no demo flows — publish draft revision {draft_rev} "
            "on Site graph to go live."
        )
    else:
        offerable_msg = (
            "No offerable flows — explore and validate flows before demoing."
        )
    checks.append(
        ReadinessCheck(
            id="offerable_flow",
            ok=offerable,
            message=offerable_msg,
            blocking=True,
        )
    )

    playlist_ok = bool(graph.demo_playlist)
    if playlist_ok:
        playlist_msg = "Demo playlist configured."
    elif draft_graph and draft_graph.demo_playlist and draft_rev is not None:
        n = len(draft_graph.demo_playlist)
        playlist_msg = (
            f"Published playlist is empty — publish draft revision {draft_rev} "
            f"({n} flow{'s' if n != 1 else ''} in draft)."
        )
    else:
        playlist_msg = "Add a demo playlist so the walkthrough knows what to show."
    checks.append(
        ReadinessCheck(
            id="playlist",
            ok=playlist_ok,
            message=playlist_msg,
            blocking=False,
        )
    )

    # Guided stubs with zero binds: demo would only show the home page.
    try:
        from navigator.automation.guided_task.apply import playlist_unbound_guided
        import yaml as _yaml

        if origin == "dashboard_test":
            rev = registry.latest_revision(product_id)
            raw = _yaml.safe_load(rev.yaml)
            if isinstance(raw, dict) and playlist_unbound_guided(raw):
                checks.append(
                    ReadinessCheck(
                        id="guided_stubs",
                        ok=False,
                        message=(
                            "Guided plan exists but no selectors are bound yet — "
                            "record the flow (Update existing) before starting a test demo."
                        ),
                        blocking=True,
                    )
                )
            else:
                checks.append(
                    ReadinessCheck(
                        id="guided_stubs",
                        ok=True,
                        message="Guided stubs bound or no unbound guided plan.",
                        blocking=False,
                    )
                )
    except Exception:  # noqa: BLE001
        pass

    k_count = _knowledge_count(product_id, chroma_path)
    sem_ok = _flow_has_semantics_or_triggers(graph)
    knowledge_ok = k_count > 0 or sem_ok
    checks.append(
        ReadinessCheck(
            id="knowledge",
            ok=knowledge_ok,
            message=(
                f"Knowledge indexed ({k_count} chunks) or flow semantics present."
                if knowledge_ok
                else "Add product knowledge or flow triggers so questions get answered."
            ),
            blocking=autonomy_mode == "adaptive",
        )
    )

    if published_rev is not None:
        try:
            from navigator.knowledge.context import retrieve_context

            stale = retrieve_context(
                "pricing demo",
                product_id,
                registry=registry,
                chroma_path=chroma_path,
            ).is_stale
            checks.append(
                ReadinessCheck(
                    id="knowledge_fresh",
                    ok=not stale,
                    message=(
                        "Knowledge matches published revision."
                        if not stale
                        else "Knowledge may be stale — republish or re-save knowledge."
                    ),
                    blocking=False,
                )
            )
        except Exception:  # noqa: BLE001
            pass

    groq_ok = bool(settings.groq_api_key)
    checks.append(
        ReadinessCheck(
            id="groq",
            ok=groq_ok,
            message=(
                "Groq key set — conversational interrupts enabled."
                if groq_ok
                else "Set NAVIGATOR_GROQ_API_KEY for conversational demos."
            ),
            blocking=True,
        )
    )

    attendee = _attendee_ok()
    checks.append(
        ReadinessCheck(
            id="attendee",
            ok=attendee,
            message=(
                "Meeting bot service reachable."
                if attendee
                else "Attendee not reachable — start meeting infrastructure."
            ),
            blocking=origin == "public_embed",
        )
    )

    return DemoReadiness(
        score=_score(checks),
        checks=tuple(checks),
        autonomy_mode=autonomy_mode,
    )


def blocking_failure(readiness: DemoReadiness) -> str | None:
    for c in readiness.checks:
        if c.blocking and not c.ok:
            return c.message
    return None


def _score(checks: list[ReadinessCheck]) -> int:
    if not checks:
        return 0
    ok = sum(1 for c in checks if c.ok)
    return int(round(100 * ok / len(checks)))
