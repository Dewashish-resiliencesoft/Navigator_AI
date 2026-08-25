"""Optional rehearse-on-publish: validate flows have steps."""

from __future__ import annotations

from dataclasses import dataclass

from navigator.automation.explore.validate import is_offerable
from navigator.knowledge.site_graph import SiteGraph, SiteGraphError


@dataclass(frozen=True)
class RehearseReport:
    flows_checked: int
    flows_ok: int
    failures: tuple[str, ...]

    @property
    def pass_rate(self) -> float:
        return self.flows_ok / self.flows_checked if self.flows_checked else 1.0

    def as_dict(self) -> dict:
        return {
            "flows_checked": self.flows_checked,
            "flows_ok": self.flows_ok,
            "pass_rate": round(self.pass_rate, 3),
            "failures": list(self.failures),
        }


def rehearse_published_graph(graph: SiteGraph) -> RehearseReport:
    """Static rehearse — ensure offerable flows parse and have steps."""
    checked = 0
    ok = 0
    failures: list[str] = []
    for page_id, page in graph.pages.items():
        for flow_id in page.flows:
            if not is_offerable(graph.flow_validation(flow_id)):
                continue
            checked += 1
            try:
                steps = list(graph.flow(page_id, flow_id))
                if steps:
                    ok += 1
                else:
                    failures.append(f"{page_id}/{flow_id}: empty flow")
            except SiteGraphError as exc:
                failures.append(f"{page_id}/{flow_id}: {exc}")
    return RehearseReport(
        flows_checked=checked,
        flows_ok=ok,
        failures=tuple(failures),
    )
