"""Write a guided plan into the draft site graph as soft stub flows."""

from __future__ import annotations

import re
from typing import Any

import yaml

from navigator.automation.guided_task.models import GuidedFlow, GuidedPlan, GuidedStep
from navigator.knowledge.site_graph import SiteGraphError, parse_site_graph

STUB_ATTR = "data-navigator-guided-stub"


def guided_stub_selector(alias: str) -> str:
    """Placeholder CSS — replaced when the Client records the real control."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", alias.strip())[:48] or "el"
    return f'[{STUB_ATTR}="{safe}"]'


def is_guided_stub_selector(css: str) -> bool:
    return STUB_ATTR in (css or "")


def _step_to_call(step: GuidedStep) -> dict[str, Any]:
    alias = step.alias
    stub = guided_stub_selector(alias)
    if step.kind == "USER_INPUT":
        return {
            "tool": "fill_field",
            "selector": alias,
            "value": "",
            "source": "user",
            "live_question": step.live_question or step.label,
            "spoken": step.spoken or step.label,
            "expects": {
                "check": "visible",
                "selector": alias,
                "timeout_ms": 5000,
            },
            "_guided_stub": True,
            "_stub_css": stub,
        }
    return {
        "tool": "click_element",
        "selector": alias,
        "spoken": step.spoken or step.label,
        "expects": {"check": "visible", "selector": "body", "timeout_ms": 3000},
        "_guided_stub": True,
        "_stub_css": stub,
        "_action_hint": step.action_hint,
    }


def _flow_calls(flow: GuidedFlow) -> list[dict[str, Any]]:
    calls = [_step_to_call(s) for s in flow.steps]
    if not calls:
        calls = [
            {
                "tool": "wait_for",
                "selector": "body",
                "timeout_ms": 5000,
                "expects": {"check": "visible", "selector": "body", "timeout_ms": 5000},
            }
        ]
    return calls


def guided_progress(raw: dict[str, Any]) -> dict[str, int]:
    """How much of the guided plan is bound to real selectors."""
    meta = raw.get("_meta") or {}
    plan_raw = meta.get("guided_task") or {}
    plan = GuidedPlan.from_meta(plan_raw)
    if plan is None or not plan.flows:
        return {
            "flows_total": 0,
            "flows_bound": 0,
            "steps_total": 0,
            "steps_bound": 0,
        }

    pages = raw.get("pages") or {}
    flows_total = len(plan.flows)
    steps_total = sum(len(f.steps) for f in plan.flows)
    flows_bound = 0
    steps_bound = 0

    for gf in plan.flows:
        page = pages.get(gf.page_id) if isinstance(pages, dict) else None
        if not isinstance(page, dict):
            continue
        selectors = page.get("selectors") or {}
        flow_steps = (page.get("flows") or {}).get(gf.flow_id) or []
        if not isinstance(flow_steps, list):
            continue
        bound_in_flow = 0
        for step in flow_steps:
            if not isinstance(step, dict):
                continue
            alias = str(step.get("selector") or "")
            if not alias:
                continue
            css = selectors.get(alias) if isinstance(selectors, dict) else None
            if css and not is_guided_stub_selector(str(css)):
                bound_in_flow += 1
        steps_bound += bound_in_flow
        if bound_in_flow >= len(gf.steps) and len(gf.steps) > 0:
            flows_bound += 1

    return {
        "flows_total": flows_total,
        "flows_bound": flows_bound,
        "steps_total": steps_total,
        "steps_bound": steps_bound,
    }


def apply_guided_plan(
    yaml_text: str,
    plan: GuidedPlan,
    *,
    page_id: str = "dashboard",
) -> str:
    """Merge plan flows into draft site graph. Does not publish."""
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise SiteGraphError("site graph must be a mapping")

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
    if "body" not in selectors:
        selectors["body"] = "body"
    flows = page.setdefault("flows", {})

    playlist = list(raw.get("demo_playlist") or [])
    next_order = max([int(p.get("order") or 0) for p in playlist if isinstance(p, dict)] + [0])

    for gf in plan.flows:
        pid = gf.page_id or page_id
        if pid != page_id:
            page = pages.setdefault(
                pid,
                {
                    "name": pid.replace("_", " ").title(),
                    "url": "/",
                    "selectors": {"body": "body"},
                    "flows": {},
                },
            )
            selectors = page.setdefault("selectors", {})
            flows = page.setdefault("flows", {})

        calls = _flow_calls(gf)
        for call in calls:
            alias = str(call.get("selector") or "")
            stub_css = str(call.get("_stub_css") or guided_stub_selector(alias))
            if alias:
                selectors[alias] = stub_css

        flows[gf.flow_id] = calls

        replaced = False
        for entry in playlist:
            if isinstance(entry, dict) and entry.get("flow_id") == gf.flow_id:
                entry["name"] = gf.name
                entry["page_id"] = pid
                replaced = True
                break
        if not replaced:
            next_order += 1
            playlist.append(
                {
                    "order": next_order,
                    "name": gf.name,
                    "page_id": pid,
                    "flow_id": gf.flow_id,
                }
            )

    raw["demo_playlist"] = playlist
    meta = raw.setdefault("_meta", {})
    meta["guided_task"] = plan.to_meta()
    meta["guided_task"]["progress"] = guided_progress(raw)

    out = yaml.safe_dump(raw, sort_keys=False)
    # Strip private stub keys before strict validation — they live only in _meta plan.
    cleaned = yaml.safe_load(out)
    if isinstance(cleaned, dict):
        for pg in (cleaned.get("pages") or {}).values():
            if not isinstance(pg, dict):
                continue
            for fl in (pg.get("flows") or {}).values():
                if not isinstance(fl, list):
                    continue
                for step in fl:
                    if isinstance(step, dict):
                        step.pop("_guided_stub", None)
                        step.pop("_stub_css", None)
                        step.pop("_action_hint", None)
    out = yaml.safe_dump(cleaned, sort_keys=False)
    parse_site_graph(out, origin="<guided-task-apply>")
    return out
