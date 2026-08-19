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
        alias = getattr(call, "selector", "")
        tool_name = call.tool
        value = getattr(call, "value", "")
        pc = call.expects.model_dump() if call.expects else {}
        # Narration from demo_script_meta
        idx = len(steps)
        spoken = graph.demo_script_spoken(flow_id, idx) or ""
        needs_approval = pc.get("check") in ("", None) or False
        step = RecordedStep(
            tool=tool_name,
            alias=alias,
            selector=graph.page(page_id).selectors.get(alias, ""),
            value=value or "",
            page_id=page_id,
            postcondition=pc,
            source="agent",
            needs_approval=needs_approval,
        )
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
