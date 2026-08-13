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
        ask_text=ask_text,
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
) -> str:
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise SiteGraphError("site graph must be a mapping")
    from navigator.automation.record_scrub import scrub_recorded_steps

    cleaned = scrub_recorded_steps(list(steps))
    draft = draft_site_graph(
        base_url=base_url, product_name=product_name, steps=cleaned
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
    phase: str = "setup"  # setup | capturing | done
    setup_discarded: int = 0
    flagged: list[dict[str, Any]] = field(default_factory=list)
    gate: CaptureGate | None = None
    narration: NarrationCapture | None = None
    save_mode: str = "new"  # new | update


_recorder_lock = threading.Lock()
_active: RecorderJob | None = None


class _MpGate:
    """CaptureGate stand-in whose phase lives in a multiprocessing Namespace."""

    def __init__(self, ns: Any, flagged: Any):
        self._ns = ns
        self.setup_discarded = 0
        self.flagged = flagged
        self.login_config_fn = None
        self.allow_login_steps = False

    @property
    def phase(self) -> str:
        return str(self._ns.phase or "setup")

    @phase.setter
    def phase(self, value: str) -> None:
        self._ns.phase = value


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
) -> None:
    """Own interpreter — uvicorn's asyncio loop breaks Playwright sync+WS."""
    from navigator.automation.login_match import LoginConfig
    from navigator.automation.record import NarrationCapture, record_session

    gate = _MpGate(ns, flagged)
    gate.allow_login_steps = allow_login
    if login_url:
        gate.login_config_fn = lambda: LoginConfig(login_url=login_url)
    try:
        record_session(
            start_url,
            out_path=Path(out_path),
            product_name=flow_name,
            headful=True,
            stop_event=stop,
            steps_out=steps,
            gate=gate,
            narration=NarrationCapture() if narrate else None,
            browser_ws=browser_ws,
        )
        ns.setup_discarded = gate.setup_discarded
    except Exception as exc:  # noqa: BLE001
        ns.error = str(exc)
        print(f"[record] worker crash:\n{traceback.format_exc()}", flush=True)


def recorder_status() -> dict[str, Any]:
    with _recorder_lock:
        if _active is None:
            return {"active": False}
        if _active.gate is not None:
            _active.phase = _active.gate.phase
            _active.setup_discarded = _active.gate.setup_discarded
            _active.flagged = list(_active.gate.flagged)
        return {
            "active": not _active.done,
            "job_id": _active.job_id,
            "flow_name": _active.flow_name,
            "flow_id": _active.flow_id,
            "steps": len(_active.steps),
            "error": _active.error,
            "done": _active.done,
            "phase": _active.phase if not _active.done else "done",
            "setup_discarded": _active.setup_discarded,
            "flagged": list(_active.flagged),
            "narrate": _active.narration is not None,
            "save_mode": _active.save_mode,
            "narration_chunks": (
                len(_active.narration.chunks) if _active.narration else 0
            ),
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
        steps = mgr.list()
        flagged = mgr.list()
        mp_stop = ctx.Event()
        job.mp_ns = ns
        job.mp_stop = mp_stop
        job.steps = steps  # type: ignore[assignment]
        gate.phase = "setup"
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
            gate.phase = "done"
            job.phase = "done"
            job.setup_discarded = gate.setup_discarded
            job.flagged = list(gate.flagged)
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
        return job


def stop_recorder() -> RecorderJob:
    with _recorder_lock:
        job = _active
        if job is None:
            raise RuntimeError("no active recording")
        job.stop.set()
        if job.mp_stop is not None:
            job.mp_stop.set()
    if job.proc is not None:
        job.proc.join(timeout=120)
    elif job.thread:
        job.thread.join(timeout=120)
    if job.gate is not None:
        job.phase = "done"
        job.setup_discarded = job.gate.setup_discarded
        job.flagged = list(job.gate.flagged)
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
