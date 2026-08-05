"""SiteGraph: the one hand-authored artifact in the system.

Maps pages -> selector aliases -> flows -> postconditions for the target site.
The agent never infers selectors; it looks them up here. A bad site graph must
fail at load time, never mid-call, so `load_site_graph` cross-checks every alias
and page reference before returning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from navigator.core.schemas import (
    Navigate,
    Persona,
    Postcondition,
    ToolCall,
    tool_selector,
)


class SiteGraphError(Exception):
    """A site graph is malformed or internally inconsistent."""


class PageSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    """Human label, used verbatim in narration."""
    url: str = ""
    """Resolved against SiteGraph.base_url."""
    selectors: dict[str, str]
    """alias -> CSS selector. Aliases are what tool calls reference."""
    flows: dict[str, tuple[ToolCall, ...]] = Field(default_factory=dict)
    # ponytail: flows are parsed and validated now, but only PLANNING reads them --
    # in Phase 1 that means replaying one; in Phase 2 the LLM picks among them.


class DemoPlaylistItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    order: int = 1
    name: str = ""
    page_id: str
    flow_id: str


class SiteGraph(BaseModel):
    # populate_by_name so `SiteGraph(meta=...)` works in code while YAML keeps
    # using the `_meta` key it already writes.
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    version: int
    site: str
    """Product slug. With `version`, this is the registry key."""
    base_url: str
    pages: dict[str, PageSpec]
    persona: Persona | None = None
    """How the agent introduces this product. Defaults from `site` when absent."""
    demo_playlist: tuple[DemoPlaylistItem, ...] = ()
    """Ordered demo flows for ops console / default walkthrough pick."""
    meta: dict[str, Any] = Field(default_factory=dict, alias="_meta")
    """Generated, Client-editable side data: narration suggestions, semantics.

    Deliberately untyped and never cross-checked. Everything in here is a
    suggestion produced by a model, so a malformed or stale entry must degrade to
    "no suggestion" rather than fail a graph that is otherwise valid and could
    still run a demo.
    """

    def effective_persona(self) -> Persona:
        return self.persona or Persona(product_name=self.site.replace("-", " "))

    def flow_semantics(self, flow_id: str) -> dict[str, Any]:
        """Generated purpose / tags / step labels for one flow, or empty."""
        section = self.meta.get("semantics")
        if not isinstance(section, dict):
            return {}
        entry = section.get(flow_id)
        return entry if isinstance(entry, dict) else {}

    def flow_validation(self, flow_id: str) -> dict[str, Any]:
        """Health-check verdict for one flow, or empty when never validated."""
        section = self.meta.get("validation")
        if not isinstance(section, dict):
            return {}
        entry = section.get(flow_id)
        return entry if isinstance(entry, dict) else {}

    def flow_narration_suggestions(self, flow_id: str) -> list[str]:
        """Explore-generated narration lines per step, or empty."""
        section = self.meta.get("narration_suggestions")
        if not isinstance(section, dict):
            return []
        entry = section.get(flow_id)
        if not isinstance(entry, list):
            return []
        return [str(x).strip() for x in entry if str(x).strip()]

    def demo_script_meta(self) -> dict[str, Any]:
        """Client-edited demo script beats under `_meta.demo_script`."""
        section = self.meta.get("demo_script")
        return section if isinstance(section, dict) else {}

    def script_spoken_override(
        self, *, flow_id: str, step_index: int
    ) -> str | None:
        """Manual spoken line for one flow step, if set in `_meta.demo_script`."""
        beats = self.demo_script_meta().get("full_demo", {})
        if not isinstance(beats, dict):
            return None
        raw_beats = beats.get("beats")
        if not isinstance(raw_beats, list):
            return None
        for beat in raw_beats:
            if not isinstance(beat, dict):
                continue
            if beat.get("spoken_source") != "manual":
                continue
            if (
                beat.get("kind") in {"flow_step", "live_input"}
                and beat.get("flow_id") == flow_id
                and beat.get("step_index") == step_index
            ):
                spoken = str(beat.get("spoken") or "").strip()
                return spoken or None
        return None

    def primary_flow(self) -> tuple[str, str] | None:
        """First playlist entry, else None."""
        if not self.demo_playlist:
            return None
        first = sorted(self.demo_playlist, key=lambda x: x.order)[0]
        return first.page_id, first.flow_id

    def page(self, page_id: str) -> PageSpec:
        try:
            return self.pages[page_id]
        except KeyError:
            known = ", ".join(sorted(self.pages)) or "<none>"
            raise SiteGraphError(
                f"unknown page {page_id!r}; site graph defines: {known}"
            ) from None

    def selector(self, page_id: str, alias: str) -> str:
        """Resolve a selector alias to CSS. Raises rather than guessing."""
        page = self.page(page_id)
        try:
            return page.selectors[alias]
        except KeyError:
            known = ", ".join(sorted(page.selectors)) or "<none>"
            raise SiteGraphError(
                f"page {page_id!r} has no selector {alias!r}; defines: {known}"
            ) from None

    def url_for(self, page_id: str) -> str:
        """Absolute URL for a page."""
        page = self.page(page_id)
        if not page.url:
            return self.base_url
        return urljoin(self.base_url, page.url)

    def flow(self, page_id: str, flow_id: str) -> tuple[ToolCall, ...]:
        page = self.page(page_id)
        try:
            return page.flows[flow_id]
        except KeyError:
            known = ", ".join(sorted(page.flows)) or "<none>"
            raise SiteGraphError(
                f"page {page_id!r} has no flow {flow_id!r}; defines: {known}"
            ) from None


def load_site_graph(path: str | Path) -> SiteGraph:
    """Read, parse, and fully validate a site graph YAML file."""
    path = Path(path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise SiteGraphError(f"site graph not found: {path}") from None
    return parse_site_graph(text, origin=str(path), relative_to=path.parent)


def parse_site_graph(
    text: str, origin: str = "<string>", relative_to: Path | None = None
) -> SiteGraph:
    """Validate site graph YAML from any source.

    The single validator in the system: the file loader, an API upload, and the
    SDK push all land here, so a customer sees the same error message however the
    graph reached us. `relative_to` resolves a schemeless base_url against a
    directory; uploads have no directory, so they must supply an absolute URL.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SiteGraphError(f"{origin}: invalid YAML: {exc}") from None

    if not isinstance(raw, dict):
        raise SiteGraphError(f"{origin}: expected a mapping at the top level")

    try:
        graph = SiteGraph.model_validate(raw)
    except ValidationError as exc:
        raise SiteGraphError(f"{origin}: {exc}") from None

    graph = graph.model_copy(
        update={"base_url": _absolute_base(graph.base_url, origin, relative_to)}
    )
    _cross_check(graph, origin)
    return graph


def _absolute_base(base_url: str, origin: str, relative_to: Path | None) -> str:
    """A schemeless base_url is a local path, relative to the graph file.

    Lets a graph point at a committed HTML fixture without hardcoding an absolute
    path. Anything with a scheme (http://, file://) is left alone. An upload has
    no containing directory, so it must give an absolute URL -- silently resolving
    it against the server's cwd would be a path-traversal foothold.
    """
    if "://" in base_url:
        return base_url
    if relative_to is None:
        raise SiteGraphError(
            f"{origin}: base_url {base_url!r} must be absolute "
            f"(http:// or https://) when the graph is not loaded from a file"
        )
    resolved = (relative_to / base_url).resolve()
    uri = resolved.as_uri()
    # urljoin only treats a base as a directory if it ends in a slash.
    return f"{uri}/" if resolved.is_dir() and not uri.endswith("/") else uri


def _cross_check(graph: SiteGraph, origin: str) -> None:
    """Validate references that Pydantic can't see: aliases and page ids.

    Per-field shape (a postcondition needing `expected`, say) is already enforced
    by Postcondition itself; this only covers cross-references.
    """
    for page_id, page in graph.pages.items():
        for flow_id, calls in page.flows.items():
            for i, call in enumerate(calls):
                where = f"{origin}: page {page_id!r}, flow {flow_id!r}, step {i}"
                _check_call(graph, page, page_id, call, where)

    for item in graph.demo_playlist:
        where = f"{origin}: demo_playlist order={item.order}"
        if item.page_id not in graph.pages:
            raise SiteGraphError(
                f"{where}: unknown page_id {item.page_id!r}"
            )
        if item.flow_id not in graph.pages[item.page_id].flows:
            raise SiteGraphError(
                f"{where}: unknown flow_id {item.flow_id!r} on page {item.page_id!r}"
            )


def _check_call(
    graph: SiteGraph,
    page: PageSpec,
    page_id: str,
    call: ToolCall,
    where: str,
) -> None:
    alias = tool_selector(call)
    if alias is not None and alias not in page.selectors:
        raise SiteGraphError(
            f"{where}: {call.tool} targets unknown selector {alias!r} "
            f"on page {page_id!r}"
        )

    if isinstance(call, Navigate):
        if call.page_id not in graph.pages:
            raise SiteGraphError(
                f"{where}: navigate targets unknown page {call.page_id!r}"
            )
        # A navigate's postcondition is checked after the move, so it resolves
        # against the destination page, not the one the flow started on.
        page, page_id = graph.pages[call.page_id], call.page_id

    _check_postcondition(page, page_id, call.expects, where)


def _check_postcondition(
    page: PageSpec, page_id: str, expects: Postcondition, where: str
) -> None:
    """A postcondition's selector must exist on the page it is checked against."""
    if expects.selector is None:
        return
    if expects.selector not in page.selectors:
        raise SiteGraphError(
            f"{where}: postcondition targets unknown selector "
            f"{expects.selector!r} on page {page_id!r}"
        )
