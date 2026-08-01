"""Ops content helpers: bio, knowledge, playlist, recorder session."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import yaml

from navigator.knowledge.company_bio import load_bio, save_bio
from navigator.knowledge.product_brief import load_product_brief, save_product_brief
from navigator.knowledge.site_graph import SiteGraph, SiteGraphError, parse_site_graph
from navigator.automation.record import RecordedStep, draft_site_graph, record_session


def playlist_from_graph(graph: SiteGraph) -> list[dict[str, Any]]:
    items = sorted(graph.demo_playlist, key=lambda x: x.order)
    if items:
        return [
            {
                "order": it.order,
                "name": it.name or it.flow_id,
                "page_id": it.page_id,
                "flow_id": it.flow_id,
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
                }
            )
            n += 1
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


def _slug_flow(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return (s[:40] or "recorded_flow")


def merge_recorded_flow(
    yaml_text: str,
    *,
    flow_name: str,
    flow_id: str,
    page_id: str,
    steps: list[RecordedStep],
    product_name: str,
    base_url: str,
) -> str:
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise SiteGraphError("site graph must be a mapping")
    draft = draft_site_graph(
        base_url=base_url, product_name=product_name, steps=steps
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
        selectors.setdefault(alias, css)
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
    flows[flow_id] = calls

    playlist = list(raw.get("demo_playlist") or [])
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
    parse_site_graph(yaml.safe_dump(raw), origin="<ops-record-merge>")
    return yaml.safe_dump(raw, sort_keys=False)


@dataclass
class RecorderJob:
    job_id: str
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    error: str | None = None
    steps: list[RecordedStep] = field(default_factory=list)
    done: bool = False
    flow_name: str = ""
    flow_id: str = ""
    start_url: str = ""
    out_path: Path | None = None


_recorder_lock = threading.Lock()
_active: RecorderJob | None = None


def recorder_status() -> dict[str, Any]:
    with _recorder_lock:
        if _active is None:
            return {"active": False}
        return {
            "active": not _active.done,
            "job_id": _active.job_id,
            "flow_name": _active.flow_name,
            "flow_id": _active.flow_id,
            "steps": len(_active.steps),
            "error": _active.error,
            "done": _active.done,
        }


def start_recorder(
    *,
    start_url: str,
    flow_name: str,
    flow_id: str | None = None,
    headful: bool = True,
    out_dir: Path | None = None,
) -> RecorderJob:
    global _active
    fid = (flow_id or _slug_flow(flow_name)).strip() or "recorded_flow"
    with _recorder_lock:
        if _active is not None and not _active.done:
            raise RuntimeError("a recording session is already running")
        out = (out_dir or Path("archives") / "recordings") / f"{fid}.yaml"
        job = RecorderJob(
            job_id=str(uuid4()),
            flow_name=flow_name,
            flow_id=fid,
            start_url=start_url,
            out_path=out,
        )
        _active = job

    def _run() -> None:
        try:
            record_session(
                start_url,
                out_path=job.out_path or Path("archives/recordings/tmp.yaml"),
                product_name=flow_name,
                headful=headful,
                stop_event=job.stop,
                steps_out=job.steps,
            )
        except Exception as exc:  # noqa: BLE001
            job.error = str(exc)
        finally:
            job.done = True

    t = threading.Thread(target=_run, name="ops-recorder", daemon=True)
    job.thread = t
    t.start()
    return job


def stop_recorder() -> RecorderJob:
    with _recorder_lock:
        job = _active
        if job is None:
            raise RuntimeError("no active recording")
        job.stop.set()
    if job.thread:
        job.thread.join(timeout=120)
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
