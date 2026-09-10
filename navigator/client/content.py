"""Ops content helpers: bio, knowledge, playlist, recorder session."""

from __future__ import annotations

import multiprocessing as mp
import re
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import yaml

from navigator.knowledge.company_bio import load_bio, save_bio
from navigator.knowledge.product_brief import load_product_brief, save_product_brief
from navigator.knowledge.site_graph import SiteGraph, SiteGraphError, parse_site_graph
from navigator.automation.record import (
    CaptureGate,
    NarrationCapture,
    RecordedStep,
    draft_site_graph,
    record_session,
)


def playlist_from_graph(graph: SiteGraph) -> list[dict[str, Any]]:
    items = sorted(graph.demo_playlist, key=lambda x: x.order)
    if items:
        return [
            {
                "order": it.order,
                "name": it.name or it.flow_id,
                "page_id": it.page_id,
                "flow_id": it.flow_id,
                **_flow_meta(graph, it.flow_id),
            }
            for it in items
        ]
    out: list[dict[str, Any]] = []
    n = 1
    for page_id, page in graph.pages.items():
        for flow_id in page.flows:
            out.append(
                {
                    "order": n,
                    "name": flow_id.replace("_", " ").title(),
                    "page_id": page_id,
                    "flow_id": flow_id,
                    **_flow_meta(graph, flow_id),
                }
            )
            n += 1
    return out


def _flow_meta(graph: SiteGraph, flow_id: str) -> dict[str, Any]:
    """Semantics + validation badges for the Flows panel. Empty keys omitted."""
    sem = graph.flow_semantics(flow_id)
    val = graph.flow_validation(flow_id)
    out: dict[str, Any] = {}
    purpose = str(sem.get("purpose") or "").strip()
    if purpose:
        out["purpose"] = purpose
    tags = sem.get("tags")
    if isinstance(tags, list) and tags:
        out["tags"] = [str(t) for t in tags if str(t).strip()]
    auto_name = str(sem.get("auto_name") or "").strip()
    if auto_name:
        out["auto_name"] = auto_name
    verdict = str(val.get("verdict") or "").strip()
    if verdict:
        out["verdict"] = verdict
        if "risk_score" in val:
            out["risk_score"] = val["risk_score"]
        if "pass_rate" in val:
            out["pass_rate"] = val["pass_rate"]

    # Count human-interaction steps in this flow
    asks = 0
    reuses = 0
    for page in graph.pages.values():
        steps = page.flows.get(flow_id)
        if steps:
            for step in steps:
                if getattr(step, "source", None) == "user":
                    asks += 1
                elif getattr(step, "value_ref", None):
                    reuses += 1
            break
    if asks:
        out["asks_visitor_count"] = asks
    if reuses:
        out["reuses_variable_count"] = reuses

    return out


def apply_playlist_to_yaml(yaml_text: str, playlist: list[dict[str, Any]]) -> str:
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise SiteGraphError("site graph must be a mapping")
    cleaned: list[dict[str, Any]] = []
    for i, row in enumerate(playlist, start=1):
        if not isinstance(row, dict):
            continue
        page_id = str(row.get("page_id") or "").strip()
        flow_id = str(row.get("flow_id") or "").strip()
        if not page_id or not flow_id:
            continue
        cleaned.append(
            {
                "order": int(row.get("order") or i),
                "name": str(row.get("name") or flow_id).strip(),
                "page_id": page_id,
                "flow_id": flow_id,
            }
        )
    cleaned.sort(key=lambda x: x["order"])
    for i, row in enumerate(cleaned, start=1):
        row["order"] = i
    raw["demo_playlist"] = cleaned
    parse_site_graph(yaml.safe_dump(raw), origin="<ops-playlist>")
    return yaml.safe_dump(raw, sort_keys=False)


_FLOW_META_KEYS = (
    "semantics",
    "narration_suggestions",
    "validation",
    "demo_script",
)


def clear_all_flows_from_yaml(yaml_text: str) -> str:
    """Drop every flow, playlist row, and flow-derived _meta (unpublished draft)."""
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise SiteGraphError("site graph must be a mapping")

    raw["demo_playlist"] = []
    pages = raw.get("pages") or {}
    if isinstance(pages, dict):
        for page in pages.values():
            if isinstance(page, dict):
                page["flows"] = {}

    meta = raw.get("_meta")
    if isinstance(meta, dict):
        for key in _FLOW_META_KEYS:
            meta.pop(key, None)

    parse_site_graph(yaml.safe_dump(raw), origin="<ops-flows-clear>")
    return yaml.safe_dump(raw, sort_keys=False)


def reset_site_graph_for_explore(yaml_text: str) -> str:
    """Minimal valid draft: persona + base_url kept, pages/flows/demo script wiped."""
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise SiteGraphError("site graph must be a mapping")

    site = str(raw.get("site") or "client").strip() or "client"
    base_url = str(raw.get("base_url") or "https://example.com/").strip()
    persona_in = raw.get("persona") if isinstance(raw.get("persona"), dict) else {}
    product_name = str(persona_in.get("product_name") or "your product").strip()
    one_liner = str(persona_in.get("one_liner") or "").strip()
    agent_name = str(persona_in.get("agent_name") or "Navigator AI").strip()
    tone = str(persona_in.get("tone") or "friendly, clear, concise").strip()

    reset: dict[str, Any] = {
        "version": raw.get("version") or 1,
        "site": site,
        "base_url": base_url,
        "persona": {
            "product_name": product_name,
            "one_liner": one_liner,
            "agent_name": agent_name,
            "tone": tone,
        },
        "demo_playlist": [],
        "pages": {
            "home": {
                "name": "Home",
                "url": "/",
                "selectors": {"body": "body"},
                "flows": {},
            }
        },
    }
    parse_site_graph(yaml.safe_dump(reset), origin="<ops-site-graph-reset>")
    return yaml.safe_dump(reset, sort_keys=False)


def _is_login_url(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return any(tok in path for tok in ("/login", "/signin", "/sign-in", "/auth"))


def _rel_url(url: str, base_url: str) -> str:
    """Prefer path relative to base_url; keep absolute when hosts differ."""
    raw = (url or "").strip() or "/"
    if not raw.startswith(("http://", "https://")):
        return raw if raw.startswith("/") else f"/{raw}"
    base = (base_url or "").strip()
    if base and raw.lower().startswith(base.rstrip("/").lower()):
        path = raw[len(base.rstrip("/")) :] or "/"
        return path if path.startswith("/") else f"/{path}"
    parsed = urlparse(raw)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _page_label(title: str, page_id: str, url: str) -> str:
    """Prefer URL path segment — explore page titles are often identical brand strings."""
    path = (urlparse(url).path if "://" in (url or "") else (url or "")) or ""
    seg = [s for s in path.split("/") if s]
    if seg:
        return seg[-1].replace("-", " ").replace("_", " ").title()
    t = " ".join((title or "").split()).strip()
    if t and " | " in t:
        # Prefer the descriptive side after brand: "ResilioHub | WhatsApp …"
        left, right = t.split(" | ", 1)
        if right.strip() and len(right.strip()) <= 72:
            return right.strip()
        t = left.strip()
    if t and len(t) <= 64 and t.lower() not in {"home", "untitled"}:
        return t
    return page_id.replace("_", " ").title() or "Page"


def _stable_page_id(url: str, used: set[str]) -> str:
    path = (urlparse(url).path if "://" in (url or "") else (url or "")) or ""
    seg = [s for s in path.split("/") if s]
    base = re.sub(r"[^a-z0-9]+", "_", (seg[-1] if seg else "home").lower()).strip("_") or "page"
    candidate = base
    n = 2
    while candidate in used:
        candidate = f"{base}_{n}"
        n += 1
    used.add(candidate)
    return candidate


_SCREEN_HINTS: dict[str, str] = {
    "dashboard": "home base for activity and shortcuts into the rest of the product",
    "inbox": "shared WhatsApp inbox so sales and support share one conversation list",
    "phonebook": "contacts and leads your team talks to on WhatsApp",
    "kanban": "pipeline board to move deals across stages",
    "wa_meta_connect": "Meta WhatsApp Business API connection",
    "wa-meta-connect": "Meta WhatsApp Business API connection",
    "instagram_connect": "Instagram account connection",
    "instagram-connect": "Instagram account connection",
    "instagram_automation": "Instagram automation rules and replies",
    "instagram-automation": "Instagram automation rules and replies",
    "rest_api": "REST API for custom integrations",
    "rest-api": "REST API for custom integrations",
    "templates": "WhatsApp message template library",
    "library": "template library for approved WhatsApp messages",
    "broadcast": "broadcast campaigns to many contacts at once",
    "chat_flow": "chatbot and automation flow builder",
    "analytics": "analytics on conversations, response time, and conversions",
    "settings": "account and workspace settings",
}


def _screen_hint(page_id: str, url: str) -> str:
    key = (page_id or "").lower()
    if key in _SCREEN_HINTS:
        return _SCREEN_HINTS[key]
    path = (urlparse(url).path if "://" in (url or "") else (url or "")).strip("/").lower()
    for tok, hint in _SCREEN_HINTS.items():
        if tok.replace("_", "-") in path or tok.replace("-", "_") in path:
            return hint
    return "a key screen in the product"


def _bio_field_map(bio: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not bio:
        return out
    for f in bio.get("fields") or []:
        if not isinstance(f, dict):
            continue
        key = str(f.get("key") or "").strip()
        val = str(f.get("value") or "").strip()
        if key and val:
            out[key] = val
    return out


def _spoken_for_page(
    *,
    name: str,
    page_id: str,
    url: str,
    bio: dict[str, str],
    opening: bool = False,
) -> str:
    hint = _screen_hint(page_id, url)
    product = (
        bio.get("company_name")
        or bio.get("products", "").split(",")[0].strip()
        or "this product"
    )
    if opening:
        about = bio.get("usp") or bio.get("about") or bio.get("key_features") or ""
        about = " ".join(about.split())[:180]
        if about:
            return (
                f"We're on {name} — {hint}. {product}: {about} "
                f"I'll walk the main screens end to end; jump in anytime."
            )
        return (
            f"We're on {name} — {hint}. I'll walk you through {product} "
            f"end to end from the knowledge we captured."
        )
    features = bio.get("key_features") or ""
    # Pull a feature clause that mentions this screen if possible.
    bit = ""
    for part in re.split(r"[,;]", features):
        p = part.strip()
        if not p:
            continue
        low = p.lower()
        if any(tok in low for tok in name.lower().split() if len(tok) > 3):
            bit = p
            break
        if page_id.replace("_", " ") in low or page_id.replace("-", " ") in low:
            bit = p
            break
    if bit:
        return f"Next: {name}. {bit}."
    return f"Next: {name} — {hint}."


def promote_topology_to_demo_yaml(
    site_yaml: str,
    topology_yaml: str,
    *,
    max_pages: int = 12,
    flow_id: str = "default_walkthrough",
    bio_fields: dict[str, str] | None = None,
    knowledge_md: str = "",
) -> str:
    """Merge explore topology pages into draft site graph + navigate walkthrough.

    Explore map alone is non-demo. This builds a live-capable draft: pages with
    body selectors, one default_walkthrough (navigate+wait per screen), playlist.
    Spoken lines prefer captured bio / knowledge over generic filler.
    """
    site_raw = yaml.safe_load(site_yaml)
    topo_raw = yaml.safe_load(topology_yaml)
    if not isinstance(site_raw, dict):
        raise SiteGraphError("site graph must be a mapping")
    if not isinstance(topo_raw, dict):
        raise SiteGraphError("topology must be a mapping")

    topo_pages = topo_raw.get("pages")
    if not isinstance(topo_pages, dict) or not topo_pages:
        raise SiteGraphError("topology has no pages to promote")

    base_url = str(
        topo_raw.get("base_url") or site_raw.get("base_url") or "https://example.com/"
    ).strip()
    if base_url and not base_url.endswith("/"):
        base_url += "/"

    bio = dict(bio_fields or {})
    persona_in = site_raw.get("persona") if isinstance(site_raw.get("persona"), dict) else {}
    product_name = str(
        bio.get("company_name")
        or persona_in.get("product_name")
        or "your product"
    ).strip()
    if " | " in product_name:
        product_name = product_name.split(" | ", 1)[0].strip() or product_name
    if product_name.lower() in {"your product", ""}:
        # Fall back to host brand from base_url
        host = (urlparse(base_url).hostname or "").split(".")[0]
        if host and host not in {"www", "app"}:
            product_name = host.replace("-", " ").title()
    one_liner = str(
        bio.get("usp") or bio.get("about") or persona_in.get("one_liner") or ""
    ).strip()
    if " | " in one_liner and len(one_liner) < 80:
        # Avoid raw HTML titles as one-liners
        one_liner = one_liner.split(" | ", 1)[-1].strip()
    if len(one_liner) > 220:
        one_liner = one_liner[:217].rsplit(" ", 1)[0] + "…"
    agent_name = str(persona_in.get("agent_name") or "Navigator AI").strip()
    tone = str(persona_in.get("tone") or "friendly, clear, concise").strip()

    pages_out: dict[str, Any] = {}
    walk_ids: list[str] = []
    used_ids: set[str] = set()
    for _pid, meta in topo_pages.items():
        if not isinstance(meta, dict):
            continue
        abs_url = str(meta.get("url") or "").strip()
        if _is_login_url(abs_url):
            continue
        rel = _rel_url(abs_url, base_url)
        page_id = _stable_page_id(abs_url or rel, used_ids)
        name = _page_label(str(meta.get("title") or ""), page_id, abs_url or rel)
        pages_out[page_id] = {
            "name": name,
            "url": rel,
            "selectors": {"body": "body"},
            "flows": {},
        }
        walk_ids.append(page_id)
        if len(walk_ids) >= max_pages:
            break

    if not walk_ids:
        raise SiteGraphError("topology pages were all login/empty — nothing to promote")

    def _anchor_score(pid: str) -> tuple[int, str]:
        u = str(pages_out[pid].get("url") or "").lower()
        n = str(pages_out[pid].get("name") or "").lower()
        score = 0
        if "dashboard" in u or "dashboard" in n:
            score -= 20
        if u in {"/", "/home", "/home/"} or n == "home":
            score -= 10
        return (score, pid)

    anchor_id = sorted(walk_ids, key=_anchor_score)[0]
    ordered = [anchor_id] + [p for p in walk_ids if p != anchor_id]

    steps: list[dict[str, Any]] = []
    first = pages_out[ordered[0]]
    steps.append(
        {
            "tool": "wait_for",
            "selector": "body",
            "timeout_ms": 8000,
            "spoken": _spoken_for_page(
                name=str(first["name"]),
                page_id=ordered[0],
                url=str(first.get("url") or ""),
                bio=bio,
                opening=True,
            ),
            "expects": {"check": "visible", "selector": "body", "timeout_ms": 8000},
        }
    )
    for pid in ordered[1:]:
        page = pages_out[pid]
        token = _url_match_token(str(page.get("url") or pid))
        spoken = _spoken_for_page(
            name=str(page["name"]),
            page_id=pid,
            url=str(page.get("url") or ""),
            bio=bio,
            opening=False,
        )
        steps.append(
            {
                "tool": "navigate",
                "page_id": pid,
                "spoken": spoken,
                "expects": {"check": "url_matches", "expected": token},
            }
        )
        steps.append(
            {
                "tool": "wait_for",
                "selector": "body",
                "timeout_ms": 5000,
                "spoken": (
                    f"Here's {page['name']} — {_screen_hint(pid, str(page.get('url') or ''))}."
                ),
                "expects": {"check": "visible", "selector": "body", "timeout_ms": 5000},
            }
        )

    pages_out[anchor_id]["flows"] = {flow_id: steps}

    # Prefer topology site slug over generic "client" so knowledge aliases resolve.
    site_name = str(topo_raw.get("site") or "").strip()
    if not site_name or site_name == "client":
        site_name = str(site_raw.get("site") or "client").strip() or "client"
    if site_name == "client":
        host = (urlparse(base_url).hostname or "").split(".")[0]
        if host and host not in {"www", "app"}:
            site_name = host

    meta: dict[str, Any] = {
        "source": "product_explore_promote",
        "promoted_pages": len(ordered),
    }
    if (knowledge_md or "").strip():
        meta["knowledge_excerpt"] = " ".join(knowledge_md.split())[:500]

    out: dict[str, Any] = {
        "version": site_raw.get("version") or 1,
        "site": site_name,
        "base_url": base_url,
        "persona": {
            "product_name": product_name,
            "one_liner": one_liner,
            "agent_name": agent_name,
            "tone": tone,
        },
        "demo_playlist": [
            {
                "order": 1,
                "name": "Default walkthrough",
                "page_id": anchor_id,
                "flow_id": flow_id,
            }
        ],
        "pages": pages_out,
        "_meta": meta,
    }
    text = yaml.safe_dump(out, sort_keys=False)
    parse_site_graph(text, origin="<promote-topology>")
    return text


def _url_match_token(url: str) -> str:
    path = (urlparse(url).path if "://" in (url or "") else (url or "")).strip("/")
    parts = [p for p in path.split("/") if p]
    return parts[-1] if parts else (path or "/")


def draft_needs_explore_promote(yaml_text: str) -> bool:
    """True when draft is stub / prior explore seed — not a recorded multi-page graph."""
    try:
        raw = yaml.safe_load(yaml_text) or {}
    except Exception:  # noqa: BLE001
        return True
    if not isinstance(raw, dict):
        return True
    meta = raw.get("_meta")
    if isinstance(meta, dict) and meta.get("source") == "product_explore_promote":
        return True
    pages = raw.get("pages")
    if not isinstance(pages, dict) or len(pages) <= 1:
        return True
    playlist = raw.get("demo_playlist") or []
    return not playlist


def resolve_topology_yaml(product_id: str, site_yaml: str = "") -> str:
    """Load topology for product_id, falling back to site / host slug files."""
    from navigator.knowledge.product_ids import product_id_aliases
    from navigator.knowledge.topology import load_topology

    site = ""
    base_url = ""
    if site_yaml.strip():
        try:
            raw = yaml.safe_load(site_yaml) or {}
        except Exception:  # noqa: BLE001
            raw = {}
        if isinstance(raw, dict):
            site = str(raw.get("site") or "").strip()
            base_url = str(raw.get("base_url") or "").strip()
    for cid in product_id_aliases(product_id, site=site, base_url=base_url):
        loaded = load_topology(cid)
        if (loaded.get("page_count") or 0) > 0 and (loaded.get("yaml") or "").strip():
            return str(loaded["yaml"])
    return ""


def promote_explore_into_draft(
    product_id: str,
    registry: Any,
    *,
    topology_yaml: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Write promoted draft revision; returns {revision, page_count, yaml}.

    force=False skips when draft already has a multi-page recorded walkthrough.
    """
    from navigator.knowledge.company_bio import load_bio
    from navigator.knowledge.product_brief import load_product_brief

    rev = registry.latest_revision(product_id)
    if not force and not draft_needs_explore_promote(rev.yaml):
        raise SiteGraphError(
            "draft already has a multi-page walkthrough — use Promote from Site graph to overwrite"
        )
    topo = (topology_yaml or "").strip() or resolve_topology_yaml(product_id, rev.yaml)
    if not topo:
        raise SiteGraphError("no explore topology to promote — run Product Explore first")

    site = ""
    base_url = ""
    try:
        raw_site = yaml.safe_load(rev.yaml) or {}
        if isinstance(raw_site, dict):
            site = str(raw_site.get("site") or "").strip()
            base_url = str(raw_site.get("base_url") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    # Host from topology when draft still says example.com / client.
    try:
        topo_raw = yaml.safe_load(topo) or {}
        if isinstance(topo_raw, dict):
            if not base_url or "example.com" in base_url.lower():
                base_url = str(topo_raw.get("base_url") or base_url).strip()
            if not site or site == "client":
                site = str(topo_raw.get("site") or site).strip()
    except Exception:  # noqa: BLE001
        pass

    bio_map = _bio_field_map(load_bio(product_id, site=site, base_url=base_url))
    knowledge_md = load_product_brief(product_id, site=site, base_url=base_url)

    new_yaml = promote_topology_to_demo_yaml(
        rev.yaml,
        topo,
        bio_fields=bio_map,
        knowledge_md=knowledge_md,
    )
    try:
        from navigator.agent_runtime.demo_graph import build_demo_graph, serialise
        from navigator.knowledge.demo_script import (
            apply_script_patch,
            compose_full_demo_script,
        )

        graph = parse_site_graph(new_yaml)
        composed = compose_full_demo_script(
            graph,
            product_id=product_id,
            knowledge_md=knowledge_md,
            bio_fields=bio_map,
            include_login=False,
            intake_enabled=True,
        )
        beats = composed.get("beats") if isinstance(composed, dict) else None
        if isinstance(beats, list) and beats:
            new_yaml = apply_script_patch(new_yaml, beats=beats, sync_flow_steps=False)
            graph = parse_site_graph(new_yaml)
        dg = build_demo_graph(graph, product_id=product_id)
        raw = yaml.safe_load(new_yaml)
        if isinstance(raw, dict):
            meta = raw.setdefault("_meta", {})
            if isinstance(meta, dict):
                meta["demo_graph"] = serialise(dg)
            new_yaml = yaml.safe_dump(raw, sort_keys=False)
            parse_site_graph(new_yaml, origin="<promote-demo-graph>")
    except Exception as exc:  # noqa: BLE001
        print(f"[promote] demo_graph/script attach skipped: {exc}", flush=True)

    saved = registry.put_site_graph(
        product_id, new_yaml, "explored", publish=False
    )
    page_count = 0
    try:
        page_count = len(parse_site_graph(new_yaml).pages)
    except SiteGraphError:
        pass
    return {
        "revision": saved.revision,
        "page_count": page_count,
        "yaml": new_yaml,
        "published": False,
    }


def remove_flow_from_yaml(
    yaml_text: str, *, flow_id: str, page_id: str | None = None
) -> str:
    """Drop a flow from the playlist and from page flow maps (unpublished draft)."""
    fid = (flow_id or "").strip()
    if not fid:
        raise SiteGraphError("flow_id required")
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise SiteGraphError("site graph must be a mapping")

    playlist = [
        p
        for p in (raw.get("demo_playlist") or [])
        if isinstance(p, dict) and str(p.get("flow_id") or "").strip() != fid
    ]
    for i, row in enumerate(playlist, start=1):
        row["order"] = i
    raw["demo_playlist"] = playlist

    pages = raw.get("pages") or {}
    if not isinstance(pages, dict):
        pages = {}
    pid = (page_id or "").strip()
    targets = [pid] if pid and pid in pages else list(pages.keys())
    for key in targets:
        page = pages.get(key)
        if not isinstance(page, dict):
            continue
        flows = page.get("flows")
        if isinstance(flows, dict) and fid in flows:
            del flows[fid]
    # If page_id was wrong/empty, still scrub every page for this flow_id.
    if pid:
        for key, page in pages.items():
            if key == pid or not isinstance(page, dict):
                continue
            flows = page.get("flows")
            if isinstance(flows, dict) and fid in flows:
                del flows[fid]

    parse_site_graph(yaml.safe_dump(raw), origin="<ops-flow-delete>")
    return yaml.safe_dump(raw, sort_keys=False)


def _slug_flow(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return (s[:40] or "recorded_flow")


def _is_stub_step(step: Any) -> bool:
    """The `wait_for body` placeholder an empty recording leaves behind."""
    if not isinstance(step, dict):
        return False
    return step.get("tool") == "wait_for" and step.get("selector") == "body"


def resolve_flow_page_id(yaml_text: str, flow_id: str) -> str | None:
    """Where a flow lives in the site graph — playlist first, then page scan."""
    fid = (flow_id or "").strip()
    if not fid:
        return None
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        return None
    for row in raw.get("demo_playlist") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("flow_id") or "").strip() != fid:
            continue
        pid = str(row.get("page_id") or "").strip()
        if pid:
            return pid
    pages = raw.get("pages") or {}
    if isinstance(pages, dict):
        for pid, page in pages.items():
            if not isinstance(page, dict):
                continue
            flows = page.get("flows")
            if isinstance(flows, dict) and fid in flows:
                return str(pid)
    return None


def _scrub_flow_from_other_pages(
    pages: dict[str, Any], flow_id: str, *, keep_page_id: str
) -> None:
    """Drop ghost copies when a replace lands on the canonical page."""
    fid = (flow_id or "").strip()
    if not fid:
        return
    for pid, page in list(pages.items()):
        if pid == keep_page_id or not isinstance(page, dict):
            continue
        flows = page.get("flows")
        if isinstance(flows, dict) and fid in flows:
            del flows[fid]


def existing_flow_step_count(yaml_text: str, page_id: str, flow_id: str) -> int:
    """Real (non-stub) steps already saved for a flow. 0 when absent.

    Update-mode callers need this to offset appended `_meta` indices so beat
    numbering stays aligned with the concatenated flow.
    """
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        return 0
    page = (raw.get("pages") or {}).get(page_id)
    if not isinstance(page, dict):
        return 0
    steps = (page.get("flows") or {}).get(flow_id)
    if not isinstance(steps, list):
        return 0
    return sum(1 for s in steps if not _is_stub_step(s))


def rebuild_yaml_narration(
    yaml_text: str,
    *,
    flow_id: str,
    ask_text: Any = None,
    product_name: str = "",
) -> str:
    """Split monologues + fill silent clicks on an already-saved flow.

    Used after record-stop (next recording) and as a one-shot on a published
    graph so live playback does not freeze on a 45s step-0 dump.
    """
    from navigator.automation.explore.runner import _attach_meta
    from navigator.automation.narration import (
        rebuild_flow_narration,
        speech_windows_payload,
    )
    from navigator.core.schemas import tool_selector

    fid = (flow_id or "").strip()
    page_id = resolve_flow_page_id(yaml_text, fid)
    if not page_id:
        raise SiteGraphError(f"flow {fid!r} not found in site graph")
    graph = parse_site_graph(yaml_text)
    calls = graph.flow(page_id, fid)
    n = len(calls)
    if n <= 0:
        return yaml_text
    lines = list(graph.flow_narration_lines(fid))
    while len(lines) < n:
        lines.append("")
    lines = lines[:n]
    clicks = graph.flow_step_clicks(fid)
    if clicks:
        times = [int(clicks.get(i, 0) or 0) for i in range(n)]
    else:
        timing = graph.flow_step_timing(fid)
        acc = 0
        times = []
        for i in range(n):
            times.append(acc)
            acc += max(0, int(timing.get(i, 0) or 0))
    hints = []
    for call in calls:
        sel = tool_selector(call)
        if sel:
            hints.append(str(sel).replace("_", " ").strip())
        else:
            hints.append(str(getattr(call, "page_id", "") or "").replace("_", " ").strip())
    new_lines, timings, windows, clicks = rebuild_flow_narration(
        lines=lines,
        step_times_ms=times,
        hints=hints,
        # Raw Client words only — never LLM-merge into "Here is {ui}".
        ask_text=None,
        product_name=product_name,
    )
    yaml_text = _attach_meta(yaml_text, "narration_suggestions", fid, new_lines)
    if timings:
        yaml_text = _attach_meta(yaml_text, "step_timing", fid, timings)
    yaml_text = _attach_meta(
        yaml_text, "step_speech", fid, speech_windows_payload(windows)
    )
    yaml_text = _attach_meta(
        yaml_text,
        "step_clicks",
        fid,
        [{"idx": i, "at_ms": int(t)} for i, t in enumerate(clicks)],
    )
    return yaml_text


def merge_recorded_flow(
    yaml_text: str,
    *,
    flow_name: str,
    flow_id: str,
    page_id: str,
    steps: list[RecordedStep],
    product_name: str,
    base_url: str,
    update_existing: bool = False,
    replace_steps: bool = False,
    agent_tasks: list[Any] | None = None,
) -> str:
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise SiteGraphError("site graph must be a mapping")
    from navigator.automation.record_scrub import scrub_recorded_steps

    cleaned = scrub_recorded_steps(list(steps))
    draft = draft_site_graph(
        base_url=base_url,
        product_name=product_name,
        steps=cleaned,
        agent_tasks=agent_tasks,
    )
    draft_pages = draft.get("pages") or {}
    if not draft_pages:
        # Still allow empty recording to register a named stub
        calls: list = []
        elements: dict = {}
    else:
        src_page = next(iter(draft_pages.values()))
        calls = list((src_page.get("flows") or {}).get("recorded_demo") or [])
        elements = dict(src_page.get("elements") or {})

    pages = raw.setdefault("pages", {})
    page = pages.setdefault(
        page_id,
        {
            "name": page_id.replace("_", " ").title(),
            "url": "/",
            "selectors": {"body": "body"},
            "flows": {},
        },
    )
    selectors = page.setdefault("selectors", {})
    for alias, css in elements.items():
        # Re-record / explore repair must replace stale CSS. setdefault kept
        # the broken selector and live demos kept missing the control.
        selectors[alias] = css
    if "body" not in selectors:
        selectors["body"] = "body"
    flows = page.setdefault("flows", {})
    # Empty flow needs at least one valid step for schema — wait_for body
    if not calls:
        calls = [
            {
                "tool": "wait_for",
                "selector": "body",
                "timeout_ms": 5000,
                "expects": {"check": "visible", "selector": "body"},
            }
        ]
    # Explore update APPENDS. Manual record update REPLACES the whole flow.
    existing = flows.get(flow_id) if update_existing else None
    if (
        update_existing
        and not replace_steps
        and isinstance(existing, list)
        and existing
    ):
        prior = [s for s in existing if not _is_stub_step(s)]
        calls = prior + calls
    flows[flow_id] = calls
    if update_existing and replace_steps:
        _scrub_flow_from_other_pages(pages, flow_id, keep_page_id=page_id)

    playlist = list(raw.get("demo_playlist") or [])
    if update_existing:
        seen = False
        deduped: list[dict[str, Any]] = []
        for entry in playlist:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("flow_id") or "").strip() != flow_id:
                deduped.append(entry)
                continue
            if not seen:
                entry["name"] = flow_name
                entry["page_id"] = page_id
                deduped.append(entry)
                seen = True
        if not seen:
            next_order = max([int(p.get("order") or 0) for p in deduped] + [0]) + 1
            deduped.append(
                {
                    "order": next_order,
                    "name": flow_name,
                    "page_id": page_id,
                    "flow_id": flow_id,
                }
            )
        playlist = deduped
    else:
        replaced = False
        for entry in playlist:
            if isinstance(entry, dict) and entry.get("flow_id") == flow_id:
                entry["name"] = flow_name
                entry["page_id"] = page_id
                replaced = True
                break
        if not replaced:
            next_order = max([int(p.get("order") or 0) for p in playlist] + [0]) + 1
            playlist.append(
                {
                    "order": next_order,
                    "name": flow_name,
                    "page_id": page_id,
                    "flow_id": flow_id,
                }
            )
    raw["demo_playlist"] = playlist
    from navigator.automation.record_studio import demo_variables_from_steps

    meta = raw.setdefault("_meta", {})
    if not isinstance(meta, dict):
        meta = {}
        raw["_meta"] = meta
    prior_vars = meta.get("demo_variables")
    by_alias: dict[str, dict[str, str]] = {}
    if isinstance(prior_vars, list):
        for row in prior_vars:
            if isinstance(row, dict) and row.get("alias"):
                by_alias[str(row["alias"])] = {
                    "alias": str(row["alias"]),
                    "label": str(row.get("label") or row["alias"]),
                    "live_question": str(row.get("live_question") or ""),
                }
    for row in demo_variables_from_steps(cleaned):
        by_alias[row["alias"]] = row
    meta["demo_variables"] = list(by_alias.values())
    if agent_tasks:
        from navigator.automation.prompt_command import (
            AgentTask,
            agent_tasks_to_meta,
            merge_agent_tasks_into_meta,
        )

        parsed: list[AgentTask] = []
        for t in agent_tasks:
            if isinstance(t, AgentTask):
                parsed.append(t)
            elif isinstance(t, dict):
                parsed.append(AgentTask.from_dict(t))
        if parsed:
            merged = merge_agent_tasks_into_meta(meta, parsed)
            meta.clear()
            meta.update(merged)
    parse_site_graph(yaml.safe_dump(raw), origin="<ops-record-merge>")
    return yaml.safe_dump(raw, sort_keys=False)


@dataclass
class RecorderJob:
    job_id: str
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    proc: Any = None
    mp_stop: Any = None
    mp_ns: Any = None
    error: str | None = None
    steps: list[RecordedStep] = field(default_factory=list)
    done: bool = False
    flow_name: str = ""
    flow_id: str = ""
    start_url: str = ""
    out_path: Path | None = None
    phase: str = "setup"  # setup | capturing | stopping | done
    setup_discarded: int = 0
    flagged: list[dict[str, Any]] = field(default_factory=list)
    gate: CaptureGate | None = None
    narration: NarrationCapture | None = None
    save_mode: str = "new"  # new | update
    hands_commands: list[dict[str, Any]] | None = None
    #: Studio Stop — dashboard must call stop_recorder to merge/save.
    needs_merge: bool = False
    #: Tenant that owns this recording (for studio auto-persist).
    product_id: str = ""
    #: Cached result from persist_recorder_job (avoid double-merge).
    persist_result: dict[str, Any] | None = None
    #: Confirmed AgentTasks (prompt channel) if gate not available.
    agent_tasks: list[Any] = field(default_factory=list)


_recorder_lock = threading.Lock()
_active: RecorderJob | None = None


class _MpGate:
    """CaptureGate stand-in whose phase lives in a multiprocessing Namespace."""

    def __init__(self, ns: Any, flagged: Any, hands_commands: Any):
        self._ns = ns
        self.setup_discarded = 0
        self.flagged = flagged
        self.hands_commands = hands_commands
        self.login_config_fn = None
        self.allow_login_steps = False
        self.stop_event = None
        self.guided_plan_meta: dict[str, Any] | None = None
        self.status_sink = None
        self.steps_ref = None
        self.last_field = None
        self.agent_tasks: list[Any] = []

    @property
    def phase(self) -> str:
        return str(self._ns.phase or "setup")

    @phase.setter
    def phase(self, value: str) -> None:
        self._ns.phase = value

    @property
    def needs_merge(self) -> bool:
        return bool(getattr(self._ns, "needs_merge", False))

    @needs_merge.setter
    def needs_merge(self, value: bool) -> None:
        self._ns.needs_merge = bool(value)


def _record_ws_worker(
    start_url: str,
    out_path: str,
    flow_name: str,
    browser_ws: str,
    narrate: bool,
    allow_login: bool,
    login_url: str,
    stop: Any,
    ns: Any,
    steps: Any,
    flagged: Any,
    hands_commands: Any,
) -> None:
    """Own interpreter — uvicorn's asyncio loop breaks Playwright sync+WS."""
    import json

    from navigator.automation.login_match import LoginConfig
    from navigator.automation.record import NarrationCapture, record_session

    gate = _MpGate(ns, flagged, hands_commands)
    gate.allow_login_steps = allow_login
    if login_url:
        gate.login_config_fn = lambda: LoginConfig(login_url=login_url)
    try:
        raw_plan = str(getattr(ns, "guided_plan_json", "") or "")
        if raw_plan:
            gate.guided_plan_meta = json.loads(raw_plan)
    except Exception:  # noqa: BLE001
        gate.guided_plan_meta = None

    def _sink(st: dict[str, Any]) -> None:
        try:
            ns.studio_status_json = json.dumps(st)
        except Exception:  # noqa: BLE001
            pass

    gate.status_sink = _sink
    narration = NarrationCapture() if narrate else None
    try:
        record_session(
            start_url,
            out_path=Path(out_path),
            product_name=flow_name,
            headful=True,
            stop_event=stop,
            steps_out=steps,
            gate=gate,
            narration=narration,
            browser_ws=browser_ws,
        )
        ns.setup_discarded = gate.setup_discarded
        # Hand narration bytes to parent before process exits.
        if narration is not None:
            audio = narration.audio()
            if audio:
                narr_path = Path(out_path).with_suffix(".narrate.bin")
                narr_path.write_bytes(audio)
                ns.narration_path = str(narr_path)
                ns.narration_mime = narration.mime or "audio/webm"
                ns.narration_language = narration.language
                ns.narration_translate_to = narration.translate_to
                print(
                    f"[record] worker wrote narration {len(audio)} bytes → {narr_path}",
                    flush=True,
                )
    except Exception as exc:  # noqa: BLE001
        ns.error = str(exc)
        print(f"[record] worker crash:\n{traceback.format_exc()}", flush=True)


def recorder_status() -> dict[str, Any]:
    with _recorder_lock:
        if _active is None:
            return {"active": False}
        job = _active
        needs_merge = bool(job.needs_merge)
        if job.mp_ns is not None:
            # WS worker owns phase / needs_merge on the shared namespace.
            try:
                job.phase = str(job.mp_ns.phase or job.phase)
            except Exception:  # noqa: BLE001
                pass
            try:
                needs_merge = needs_merge or bool(getattr(job.mp_ns, "needs_merge", False))
            except Exception:  # noqa: BLE001
                pass
            try:
                job.setup_discarded = int(getattr(job.mp_ns, "setup_discarded", 0) or 0)
            except Exception:  # noqa: BLE001
                pass
        elif job.gate is not None:
            job.phase = job.gate.phase
            job.setup_discarded = job.gate.setup_discarded
            job.flagged = list(job.gate.flagged)
            needs_merge = needs_merge or bool(getattr(job.gate, "needs_merge", False))
        job.needs_merge = needs_merge
        # Stay "active" while merge pending so dashboard poll keeps running.
        # Once done (incl. studio auto-persist), never keep active via stale flag.
        if job.done:
            needs_merge = False
            job.needs_merge = False
        active = (not job.done) or needs_merge
        phase = job.phase
        if needs_merge and phase not in {"stopping", "done"}:
            phase = "stopping"
        if job.done and not needs_merge:
            phase = "done"
        return {
            "active": active,
            "job_id": job.job_id,
            "flow_name": job.flow_name,
            "flow_id": job.flow_id,
            "steps": len(job.steps),
            "error": job.error,
            "done": job.done and not needs_merge,
            "phase": phase,
            "setup_discarded": job.setup_discarded,
            "flagged": list(job.flagged),
            "narrate": job.narration is not None,
            "save_mode": job.save_mode,
            "narration_chunks": (
                len(job.narration.chunks) if job.narration else 0
            ),
            "needs_merge": needs_merge,
        }


def start_recorder(
    *,
    start_url: str,
    flow_name: str,
    flow_id: str | None = None,
    headful: bool = True,
    out_dir: Path | None = None,
    login_config_fn: Any = None,
    narrate: bool = False,
    save_mode: str = "new",
    browser_ws: str = "",
    guided_plan_meta: dict[str, Any] | None = None,
    product_id: str = "",
) -> RecorderJob:
    global _active
    mode = (save_mode or "new").strip().lower()
    if mode not in {"new", "update"}:
        raise RuntimeError("save_mode must be 'new' or 'update'")
    if mode == "update" and not (flow_id or "").strip():
        raise RuntimeError("flow_id required when save_mode is update")
    fid = (flow_id or _slug_flow(flow_name)).strip() or "recorded_flow"
    with _recorder_lock:
        if _active is not None and not _active.done:
            raise RuntimeError("a recording session is already running")
        out = (out_dir or Path("archives") / "recordings") / f"{fid}.yaml"
        from navigator.automation.login_match import name_suggests_login_walkthrough

        gate = CaptureGate(
            phase="setup",
            login_config_fn=login_config_fn,
            allow_login_steps=name_suggests_login_walkthrough(fid, flow_name),
            guided_plan_meta=guided_plan_meta,
        )
        narration = NarrationCapture() if narrate else None
        job = RecorderJob(
            job_id=str(uuid4()),
            flow_name=flow_name,
            flow_id=fid,
            start_url=start_url,
            out_path=out,
            phase="setup",
            gate=gate,
            narration=narration,
            save_mode=mode,
            product_id=(product_id or "").strip(),
        )
        _active = job

    if (browser_ws or "").strip():
        login_url = ""
        if login_config_fn is not None:
            try:
                login_url = str(login_config_fn().login_url or "")
            except Exception:  # noqa: BLE001
                login_url = ""
        ctx = mp.get_context("spawn")
        mgr = ctx.Manager()
        ns = mgr.Namespace()
        ns.phase = "setup"
        ns.error = ""
        ns.setup_discarded = 0
        ns.studio_status_json = ""
        ns.needs_merge = False
        ns.phase_seq = 0
        import json as _json

        ns.guided_plan_json = _json.dumps(guided_plan_meta or {})
        ns.narration_path = ""
        ns.narration_mime = ""
        ns.narration_language = "auto"
        ns.narration_translate_to = "same"
        ns.steps_json = "[]"
        ns.step_count = 0
        steps = mgr.list()
        flagged = mgr.list()
        hands_commands = mgr.list()
        mp_stop = ctx.Event()
        job.mp_ns = ns
        job.mp_stop = mp_stop
        job.steps = steps  # type: ignore[assignment]
        job.hands_commands = hands_commands  # type: ignore[assignment]
        gate.hands_commands = hands_commands  # type: ignore[assignment]

        def _main_sink(st: dict[str, Any]) -> None:
            import json

            try:
                ns.studio_status_json = json.dumps(st)
                ph = str((st or {}).get("phase") or "")
                if ph:
                    ns.phase = ph
                if (st or {}).get("needs_merge"):
                    ns.needs_merge = True
            except Exception:  # noqa: BLE001
                pass

        gate.status_sink = _main_sink
        proc = ctx.Process(
            target=_record_ws_worker,
            kwargs={
                "start_url": start_url,
                "out_path": str(job.out_path or Path("archives/recordings/tmp.yaml")),
                "flow_name": flow_name,
                "browser_ws": browser_ws.strip(),
                "narrate": narrate,
                "allow_login": gate.allow_login_steps,
                "login_url": login_url,
                "stop": mp_stop,
                "ns": ns,
                "steps": steps,
                "flagged": flagged,
                "hands_commands": hands_commands,
            },
            name="ops-recorder-ws",
            daemon=False,
        )
        job.proc = proc
        proc.start()

        def _wait_proc() -> None:
            proc.join()
            job.error = str(ns.error or "") or None
            job.setup_discarded = int(ns.setup_discarded or 0)
            job.flagged = list(flagged)
            job.steps = list(steps)
            needs_merge = bool(getattr(ns, "needs_merge", False))
            job.needs_merge = needs_merge
            _hydrate_steps_from_worker(job)
            _hydrate_narration_from_worker(job)
            if job.done and job.persist_result is not None:
                return
            if needs_merge:
                job.phase = "stopping"
                if job.gate is not None:
                    job.gate.phase = "stopping"
                    job.gate.needs_merge = True
                print(
                    "[record] studio Stop — auto-persisting flow (needs_merge)",
                    flush=True,
                )
                try:
                    persist_recorder_job(job)
                except Exception as exc:  # noqa: BLE001
                    print(f"[record] auto-persist crashed: {exc}", flush=True)
                    job.done = True
                    job.phase = "done"
                    job.error = (job.error or "") or str(exc)
            else:
                # Dashboard Stop owns persist; just mark worker finished.
                if not job.done:
                    job.phase = "done"
                    gate.phase = "done"
                    job.done = True
            if job.error:
                print(f"[record] local Chrome worker failed: {job.error}", flush=True)

        threading.Thread(target=_wait_proc, name="ops-recorder-wait", daemon=True).start()
        return job

    def _run() -> None:
        try:
            record_session(
                start_url,
                out_path=job.out_path or Path("archives/recordings/tmp.yaml"),
                product_name=flow_name,
                headful=headful,
                stop_event=job.stop,
                steps_out=job.steps,
                gate=gate,
                narration=narration,
            )
        except Exception as exc:  # noqa: BLE001
            job.error = str(exc)
            print(f"[record] thread crash:\n{traceback.format_exc()}", flush=True)
        finally:
            needs_merge = bool(getattr(gate, "needs_merge", False))
            job.needs_merge = needs_merge
            job.setup_discarded = gate.setup_discarded
            job.flagged = list(gate.flagged)
            if job.done and job.persist_result is not None:
                return
            if needs_merge:
                gate.phase = "stopping"
                job.phase = "stopping"
                print(
                    "[record] studio Stop — auto-persisting flow (needs_merge)",
                    flush=True,
                )
                try:
                    persist_recorder_job(job)
                except Exception as exc:  # noqa: BLE001
                    print(f"[record] auto-persist crashed: {exc}", flush=True)
                    job.done = True
                    job.phase = "done"
                    job.error = (job.error or "") or str(exc)
            else:
                gate.phase = "done"
                job.phase = "done"
                job.done = True

    t = threading.Thread(target=_run, name="ops-recorder", daemon=True)
    job.thread = t
    t.start()
    return job


def begin_capture() -> RecorderJob:
    """Flip setup → capturing. Nothing before this enters the saved flow."""
    with _recorder_lock:
        job = _active
        if job is None or job.done:
            raise RuntimeError("no active recording")
        if job.gate is None:
            raise RuntimeError("recording has no capture gate")
        job.gate.phase = "capturing"
        job.phase = "capturing"
        if job.mp_ns is not None:
            job.mp_ns.phase = "capturing"
            try:
                # Nudge worker overlay on next status tick.
                job.mp_ns.phase_seq = int(getattr(job.mp_ns, "phase_seq", 0) or 0) + 1
            except Exception:  # noqa: BLE001
                pass
        print("[record] begin_capture → capturing", flush=True)
        return job


def _job_agent_tasks(job: RecorderJob) -> list[Any]:
    """Confirmed prompt-command tasks from gate / narration / job."""
    tasks: list[Any] = []
    if job.gate is not None:
        tasks = list(getattr(job.gate, "agent_tasks", None) or [])
    if not tasks and getattr(job, "narration", None) is not None:
        tasks = list(getattr(job.narration, "agent_tasks", None) or [])
    if not tasks:
        tasks = list(getattr(job, "agent_tasks", None) or [])
    return tasks


def _hydrate_narration_from_worker(job: RecorderJob) -> None:
    """Pull narration audio written by the WS worker into ``job.narration``.

    MediaRecorder chunks live in the child process; without this, STT never runs
    and the demo script has no Client-spoken lines.
    """
    ns = job.mp_ns
    if ns is None:
        return
    path = str(getattr(ns, "narration_path", "") or "").strip()
    if not path:
        return
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        print(f"[record] narration file unreadable: {exc}", flush=True)
        return
    if not raw:
        return
    if job.narration is None:
        job.narration = NarrationCapture()
    job.narration.chunks = [raw]
    job.narration.mime = str(getattr(ns, "narration_mime", "") or job.narration.mime or "")
    lang = str(getattr(ns, "narration_language", "") or "").strip()
    if lang:
        job.narration.language = lang
    tr = str(getattr(ns, "narration_translate_to", "") or "").strip()
    if tr:
        job.narration.translate_to = tr
    print(f"[record] hydrated narration {len(raw)} bytes from worker", flush=True)


def _hydrate_steps_from_worker(job: RecorderJob) -> None:
    """Recover steps from Namespace JSON when Manager list is empty after kill."""
    if job.steps:
        return
    ns = job.mp_ns
    if ns is None:
        return
    import json

    from navigator.automation.record import recorded_step_from_dict

    raw = str(getattr(ns, "steps_json", "") or "").strip()
    if not raw or raw == "[]":
        return
    try:
        rows = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        print(f"[record] steps_json parse failed: {exc}", flush=True)
        return
    if not isinstance(rows, list) or not rows:
        return
    restored: list[RecordedStep] = []
    for row in rows:
        if isinstance(row, dict):
            restored.append(recorded_step_from_dict(row))
    if restored:
        job.steps = restored
        print(f"[record] hydrated {len(restored)} steps from worker snapshot", flush=True)


def persist_recorder_job(
    job: RecorderJob,
    *,
    page_id: str = "dashboard",
) -> dict[str, Any]:
    """Merge recorded steps into the Client draft site graph.

    Safe to call from the dashboard stop route or from the worker wait thread
    after studio Stop (needs_merge). Idempotent via ``job.persist_result``.

    Refuses to write when zero steps were captured — an empty replace would
    wipe a real flow down to a stub ``wait_for body``.
    """
    if job.persist_result is not None:
        return job.persist_result

    # Snapshot shared Manager list before anything else (WS worker may die).
    try:
        job.steps = list(job.steps)
    except Exception:  # noqa: BLE001
        pass
    _hydrate_steps_from_worker(job)
    _hydrate_narration_from_worker(job)

    base_out: dict[str, Any] = {
        "ok": True,
        "steps": len(job.steps),
        "error": job.error,
        "flow_id": job.flow_id,
        "flagged": list(getattr(job, "flagged", []) or []),
        "setup_discarded": int(getattr(job, "setup_discarded", 0) or 0),
        "phase": "done",
        "narrated_steps": 0,
        "published": False,
        "playlist": [],
        "revision": None,
    }
    product_id = (job.product_id or "").strip()
    if not product_id:
        base_out["error"] = (base_out.get("error") or "") or "missing product_id"
        base_out["ok"] = False
        job.persist_result = base_out
        return base_out

    if not job.steps:
        msg = (
            "0 steps captured — not saving (would wipe the flow). "
            "Click “Start capturing this flow” (or start the mic — that also "
            "starts capture), then click/fill the product UI, then Stop."
        )
        print(f"[record] {msg}", flush=True)
        base_out["ok"] = False
        base_out["error"] = (base_out.get("error") or "") or msg
        job.persist_result = base_out
        job.needs_merge = False
        job.done = True
        job.phase = "done"
        return base_out

    try:
        from navigator.app.deps import get_registry, get_vault
        from navigator.app.route_helpers import _reject_login_in_yaml
        from navigator.app.routers.client_api import _attach_recorded_narration

        registry = get_registry()
        vault = get_vault()
        rev = registry.latest_revision(product_id)
        persona = parse_site_graph(rev.yaml).effective_persona()
        pid = page_id or "dashboard"
        update = getattr(job, "save_mode", "new") == "update"
        if update:
            resolved = resolve_flow_page_id(rev.yaml, job.flow_id)
            if resolved:
                pid = resolved
        new_yaml = merge_recorded_flow(
            rev.yaml,
            flow_name=job.flow_name,
            flow_id=job.flow_id,
            page_id=pid,
            steps=list(job.steps),
            product_name=persona.product_name,
            base_url=recording_base_url(job.start_url),
            update_existing=update,
            replace_steps=update,
            agent_tasks=_job_agent_tasks(job),
        )
        new_yaml, narrated = _attach_recorded_narration(
            new_yaml,
            job,
            update_existing=update,
            replace_steps=update,
        )
        _reject_login_in_yaml(
            product_id,
            new_yaml,
            vault,
            allow_flows=frozenset({(pid, job.flow_id)}),
        )
        rev = registry.put_site_graph(product_id, new_yaml, "recorded", publish=False)
        graph = parse_site_graph(new_yaml)
        playlist = playlist_from_graph(graph)
        base_out.update(
            {
                "playlist": playlist,
                "revision": rev.revision,
                "narrated_steps": narrated,
                "steps": len(job.steps),
            }
        )
        print(
            f"[record] persisted {len(job.steps)} steps → flow {job.flow_id!r} "
            f"rev {rev.revision}",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[record] persist failed: {exc}\n{traceback.format_exc()}", flush=True)
        base_out["error"] = (base_out.get("error") or "") or str(exc)

    job.persist_result = base_out
    job.needs_merge = False
    job.done = True
    job.phase = "done"
    if job.gate is not None:
        job.gate.needs_merge = False
        job.gate.phase = "done"
    if job.mp_ns is not None:
        try:
            job.mp_ns.needs_merge = False
            job.mp_ns.phase = "done"
        except Exception:  # noqa: BLE001
            pass
    return base_out


def stop_recorder() -> RecorderJob:
    """Signal the browser session to end and wait briefly for the worker/thread.

    Does not merge by itself — callers use ``persist_recorder_job``. If studio
    Stop already auto-persisted, returns the finished job immediately.
    """
    with _recorder_lock:
        job = _active
        if job is None:
            raise RuntimeError("no active recording")
        if job.done:
            return job
        job.stop.set()
        if job.mp_stop is not None:
            job.mp_stop.set()
        if job.gate is not None:
            job.gate.phase = "stopping"
            job.gate.needs_merge = False
        job.phase = "stopping"
        if job.mp_ns is not None:
            try:
                job.mp_ns.phase = "stopping"
                job.mp_ns.needs_merge = False
            except Exception:  # noqa: BLE001
                pass
    # Wait for Playwright + narration flush. Kill only as last resort.
    if job.proc is not None:
        job.proc.join(timeout=45)
        if job.proc.is_alive():
            print("[record] worker slow after stop — terminating", flush=True)
            # Snapshot shared steps BEFORE kill (Manager may die with process).
            try:
                job.steps = list(job.steps)
            except Exception:  # noqa: BLE001
                pass
            _hydrate_steps_from_worker(job)
            _hydrate_narration_from_worker(job)
            job.proc.terminate()
            job.proc.join(timeout=5)
            if job.proc.is_alive():
                try:
                    job.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
                job.proc.join(timeout=2)
        try:
            job.steps = list(job.steps)
        except Exception:  # noqa: BLE001
            pass
        _hydrate_steps_from_worker(job)
        _hydrate_narration_from_worker(job)
    elif job.thread:
        job.thread.join(timeout=45)
    if job.gate is not None:
        job.setup_discarded = job.gate.setup_discarded
        job.flagged = list(job.gate.flagged)
        job.gate.needs_merge = False
        job.gate.phase = "done"
    if job.mp_ns is not None:
        try:
            job.mp_ns.needs_merge = False
            job.mp_ns.phase = "done"
            job.setup_discarded = int(getattr(job.mp_ns, "setup_discarded", 0) or job.setup_discarded)
        except Exception:  # noqa: BLE001
            pass
    if not job.done:
        job.done = True
        job.phase = "done"
        job.needs_merge = False
    return job


def recording_base_url(start_url: str) -> str:
    parsed = urlparse(start_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def apply_base_url_to_yaml(yaml_text: str, base_url: str) -> str:
    """Set site graph ``base_url`` (the product origin demos navigate to)."""
    cleaned = (base_url or "").strip()
    if not cleaned.startswith(("http://", "https://")):
        raise SiteGraphError("base_url must be an absolute http(s) URL")
    parsed = urlparse(cleaned)
    if not parsed.netloc or parsed.netloc.lower() in {"example.com", "www.example.com"}:
        raise SiteGraphError("set your real product domain — not example.com")
    # Origin only: path/query belong on page urls, not base_url.
    origin = f"{parsed.scheme}://{parsed.netloc}/"
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise SiteGraphError("site graph must be a mapping")
    raw["base_url"] = origin
    parse_site_graph(yaml.safe_dump(raw), origin="<ops-base-url>")
    return yaml.safe_dump(raw, sort_keys=False)


def guided_task_meta(yaml_text: str) -> dict[str, Any]:
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        return {}
    meta = raw.get("_meta") or {}
    gt = meta.get("guided_task")
    return dict(gt) if isinstance(gt, dict) else {}


def guided_task_status(yaml_text: str) -> dict[str, Any]:
    from navigator.automation.guided_task.apply import guided_progress
    from navigator.automation.guided_task.models import GuidedPlan

    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        return {"has_plan": False}
    gt = guided_task_meta(yaml_text)
    plan = GuidedPlan.from_meta(gt)
    progress = guided_progress(raw)
    pct = 0
    if progress["steps_total"] > 0:
        pct = int(100 * progress["steps_bound"] / progress["steps_total"])
    return {
        "has_plan": plan is not None and bool(plan.flows),
        "task_id": gt.get("task_id"),
        "prompt": gt.get("prompt"),
        "flows": [
            {
                "name": f.name,
                "flow_id": f.flow_id,
                "steps": len(f.steps),
                "step_list": [
                    {"kind": s.kind, "label": s.label, "alias": s.alias}
                    for s in f.steps
                ],
            }
            for f in (plan.flows if plan else ())
        ],
        "progress": progress,
        "percent_bound": pct,
    }


def enqueue_hands_command(cmd: dict[str, Any]) -> None:
    """Queue a guided-hands command for the active recorder worker."""
    with _recorder_lock:
        job = _active
        if job is None or job.done:
            raise RuntimeError("no active recording")
        cmds = job.hands_commands
        if cmds is None and job.gate is not None:
            cmds = getattr(job.gate, "hands_commands", None)
        if cmds is None:
            raise RuntimeError("recorder has no hands command channel")
        cmds.append(cmd)


def set_recorder_guided_plan(plan_meta: dict[str, Any] | None) -> None:
    """Attach guided plan meta so browser Start hands / worker can start session."""
    with _recorder_lock:
        job = _active
        if job is None or job.done:
            return
        if job.gate is not None:
            job.gate.guided_plan_meta = plan_meta
        if job.mp_ns is not None:
            import json

            try:
                job.mp_ns.guided_plan_json = json.dumps(plan_meta or {})
            except Exception:  # noqa: BLE001
                pass


def read_hands_status() -> dict[str, Any]:
    """Hands status from worker sink (WS) or in-process session (thread)."""
    import json

    from navigator.automation.guided_task.session import get_guided_hands_session

    with _recorder_lock:
        job = _active
        if job is not None and job.mp_ns is not None:
            raw = getattr(job.mp_ns, "studio_status_json", "") or ""
            if raw:
                try:
                    st = json.loads(raw)
                    if isinstance(st, dict) and "hands" in st:
                        return dict(st.get("hands") or {"active": False})
                except Exception:  # noqa: BLE001
                    pass
        sess = get_guided_hands_session()
        return sess.status_dict() if sess is not None else {"active": False}


def drain_pending_guided_plan(registry: Any, product_id: str) -> bool:
    """Apply plan_meta from recorder worker (Ask-visitor) onto draft site graph."""
    import json

    from navigator.automation.guided_task.apply import apply_guided_plan
    from navigator.automation.guided_task.models import GuidedPlan

    meta: dict[str, Any] | None = None
    with _recorder_lock:
        job = _active
        if job is None or job.done:
            return False
        dirty = False
        if job.mp_ns is not None:
            try:
                st = json.loads(getattr(job.mp_ns, "studio_status_json", "") or "{}")
            except Exception:  # noqa: BLE001
                st = {}
            dirty = bool(st.get("plan_dirty"))
            plan_meta = st.get("plan_meta")
            if not plan_meta:
                raw = getattr(job.mp_ns, "guided_plan_json", "") or ""
                try:
                    plan_meta = json.loads(raw) if raw else None
                except Exception:  # noqa: BLE001
                    plan_meta = None
            if dirty and isinstance(plan_meta, dict):
                meta = plan_meta
                st["plan_dirty"] = False
                try:
                    job.mp_ns.studio_status_json = json.dumps(st)
                    job.mp_ns.guided_plan_json = json.dumps(plan_meta)
                except Exception:  # noqa: BLE001
                    pass
        elif job.gate is not None and getattr(job.gate, "guided_plan_meta", None):
            # Thread mode: apply whenever gate meta was refreshed by mark_ask.
            flag = getattr(job.gate, "_plan_dirty", False)
            if flag:
                meta = dict(job.gate.guided_plan_meta or {})
                job.gate._plan_dirty = False  # type: ignore[attr-defined]

    if not meta:
        return False
    plan = GuidedPlan.from_meta(meta)
    if plan is None or not plan.flows:
        return False
    try:
        rev = registry.latest_revision(product_id)
        new_yaml = apply_guided_plan(rev.yaml, plan)
        registry.put_site_graph(product_id, new_yaml, "guided_ask", publish=False)
        set_recorder_guided_plan(plan.to_meta())
        print("[guided-ask] draft site graph updated from Ask visitor", flush=True)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[guided-ask] draft apply failed: {exc}", flush=True)
        return False
