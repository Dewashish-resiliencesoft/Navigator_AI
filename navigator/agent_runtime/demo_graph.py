"""Phase-4: DemoGraph builder — derives 'how to demo' from SiteGraph.

Produces a DemoGraph from an existing SiteGraph revision. The DemoGraph
carries objectives, narration, interaction, safety, and recovery for every
flow in the playlist. It is stored as ``_meta.demo_graph`` in the site graph
YAML and is re-generated when:
  - Auto-explore finishes
  - A recorded flow is compiled
  - A client manually edits narration in the dashboard

The SiteGraph remains the structural authority ('what exists').
The DemoGraph is the semantic demo layer ('how to demo it').
"""

from __future__ import annotations

from typing import Any

from navigator.agent_runtime.demo_compiler import compile_flow
from navigator.agent_runtime.models import (
    DemoFlow,
    DemoGraph,
    DemoMode,
    DemoPlaylist,
    DemoStep,
)
from navigator.automation.record import RecordedStep
from navigator.knowledge.site_graph import SiteGraph


def _flow_objective(graph: SiteGraph, flow_id: str) -> str:
    """Derive flow objective from semantics meta."""
    sem = graph.flow_semantics(flow_id)
    purpose = (sem.get("purpose") or "").strip()
    label = (sem.get("label") or sem.get("auto_name") or "").strip()
    if purpose:
        return purpose
    if label:
        return f"Demonstrate {label.lower()}"
    return f"Demonstrate {flow_id.replace('_', ' ')}"


def _steps_from_graph(graph: SiteGraph, page_id: str, flow_id: str) -> list[RecordedStep]:
    """Extract RecordedStep-like objects from the SiteGraph flow tool calls."""
    try:
        page = graph.page(page_id)
    except Exception:  # noqa: BLE001
        return []
    calls = page.flows.get(flow_id, ())
    steps: list[RecordedStep] = []
    for call in calls:
        tool_name = call.tool
        dest_page = (
            str(getattr(call, "page_id", "") or "").strip()
            if tool_name == "navigate"
            else page_id
        ) or page_id
        alias = getattr(call, "selector", "") or (
            dest_page if tool_name == "navigate" else ""
        )
        value = getattr(call, "value", "")
        pc = call.expects.model_dump() if call.expects else {}
        spoken = (getattr(call, "spoken", None) or "").strip()
        if not spoken:
            try:
                spoken = (graph.script_spoken_override(flow_id=flow_id, step_index=len(steps)) or "").strip()
            except Exception:  # noqa: BLE001
                spoken = ""
        needs_approval = pc.get("check") in ("", None) or False
        sel_page = dest_page if dest_page in graph.pages else page_id
        step = RecordedStep(
            tool=tool_name,
            alias=alias,
            selector=graph.page(sel_page).selectors.get(alias, "") if alias else "",
            value=value or "",
            page_id=dest_page,
            postcondition=pc,
            source="agent",
            input_name=getattr(call, "input_name", None),
            input_type=getattr(call, "input_type", "text"),
            prompt=getattr(call, "prompt", None),
            fallback_value=getattr(call, "fallback_value", None),
            needs_approval=needs_approval,
        )
        if getattr(call, "source", "agent") == "user":
            step.source = "user"
            step.live_question = getattr(call, "live_question", None)
        # Attach narration
        object.__setattr__(step, "spoken", spoken) if hasattr(step, "__dataclass_fields__") else None
        try:
            step.spoken = spoken  # type: ignore[attr-defined]
        except AttributeError:
            pass
        steps.append(step)
    return steps


def build_demo_graph(
    graph: SiteGraph,
    *,
    product_id: str = "",
    mode: DemoMode = DemoMode.automated,
) -> DemoGraph:
    """Build a DemoGraph from the current SiteGraph revision."""
    flows: dict[str, DemoFlow] = {}

    for item in sorted(graph.demo_playlist, key=lambda x: x.order):
        flow_id = item.flow_id
        page_id = item.page_id
        objective = _flow_objective(graph, flow_id)

        recorded = _steps_from_graph(graph, page_id, flow_id)
        demo_steps = compile_flow(recorded, flow_id=flow_id, objective=objective)

        flows[flow_id] = DemoFlow(
            flow_id=flow_id,
            objective=objective,
            audience="",
            priority=item.order,
            steps=demo_steps,
        )

    playlist = DemoPlaylist(
        mode=mode,
        flows=[item.flow_id for item in sorted(graph.demo_playlist, key=lambda x: x.order)],
    )

    return DemoGraph(
        version=1,
        product_id=product_id or graph.site,
        flows=flows,
        playlist=playlist,
    )


def serialise(demo_graph: DemoGraph) -> dict[str, Any]:
    """Serialise to dict for storage under ``_meta.demo_graph``."""
    return demo_graph.model_dump(mode="json")


def deserialise(data: dict[str, Any]) -> DemoGraph:
    return DemoGraph.model_validate(data)
