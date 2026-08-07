"""The exploration loop: PERCEIVE -> REASON -> GUARDRAIL -> ACT -> VERIFY.

Actions go through the existing four-tool interface (`automation.browser.tools`),
which resolves selector *aliases* against a SiteGraph. Exploration therefore
builds an ephemeral single-page graph as it discovers elements and grows it in
place -- no new action interface, and the same alias discipline the live agent
is held to.

Output is `list[RecordedStep]`, byte-for-byte the manual recorder's currency, so
`merge_recorded_flow` consumes it unchanged and both paths land in the same
review-before-activate gate.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import uuid4

from navigator.automation.explore import history, page_plan, perceive, reason, semantics
from navigator.automation.external_links import (
    element_is_external,
    is_external_url,
    is_product_surface,
    recover_product_surface,
    revert_external_navigation,
)
from navigator.automation.explore.diagnose import classify, looks_nav_stalled
from navigator.automation.explore.episode import EpisodeStore, StepAttempt
from navigator.automation.explore.fields import classify_field, question_for
from navigator.automation.explore.guardrail import FlaggedAction, classify_action
from navigator.automation.explore.repair import (
    RepairCtx,
    click_postcondition,
    click_verify_passed,
    run_ladder,
)
from navigator.automation.explore.session import (
    ExplorationSession,
    FieldDecision,
    element_key,
    fingerprint,
)
from navigator.automation.record import (
    RecordedStep,
    guess_postcondition,
    junk_record_reason,
    prefer_selector,
)
from navigator.core.schemas import (
    ClickElement,
    FillField,
    Postcondition,
    ToolResult,
    WaitFor,
)

EXPLORE_PAGE_ID = "main"


@dataclass
class ExplorerDeps:
    """Everything the loop touches that isn't the session itself.

    Injectable so the whole loop is testable with a fake page and stub models --
    no browser, no network.
    """

    page: Any
    ask_text: Callable[[str], str] | None = None
    ask_vision: Callable[[str, str], str] | None = None
    guard_judge: Callable[[str], str] | None = None
    field_judge: Callable[[str], str] | None = None
    corrections: tuple[str, ...] = ()
    on_action: Callable[[RecordedStep, ToolResult, Any], None] | None = None
    execute: Callable[..., tuple[ToolResult, str]] | None = None
    verify: Callable[..., Any] | None = None
    episode: EpisodeStore | None = None
    #: Text model for semantic step labels. None disables labelling entirely --
    #: separate from `ask_text` so the cost is opted into, not inherited.
    label_ask: Callable[[str], str] | None = None
    #: Prior-run unrepaired failures: element_key → count. Empty = no history.
    known_bad: dict[str, int] = field(default_factory=dict)
    #: Prior-run successful repairs: (url_path, kind) → tactic name.
    proven_tactics: dict[tuple[str, str], str] = field(default_factory=dict)


class _LiveGraph:
    """Minimal SiteGraph stand-in: alias -> CSS, grown as elements are found.

    `tools.execute` only ever calls `.selector()` and `.url_for()` on the graph,
    so this satisfies the real interface without materialising a full YAML graph
    mid-run.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.selectors: dict[str, str] = {"body": "body"}

    def add(self, alias: str, css: str) -> None:
        self.selectors.setdefault(alias, css)

    def selector(self, page_id: str, alias: str) -> str:
        from navigator.knowledge.site_graph import SiteGraphError

        try:
            return self.selectors[alias]
        except KeyError:
            raise SiteGraphError(f"unknown alias {alias!r}") from None

    def url_for(self, page_id: str) -> str:
        return self.base_url


def explore(session: ExplorationSession, deps: ExplorerDeps) -> list[RecordedStep]:
    """Run the loop until a budget bound or the stop signal ends it."""
    from navigator.automation.browser import tools as browser_tools
    from navigator.automation.browser import verify as browser_verify

    execute = deps.execute or browser_tools.execute
    verify = deps.verify or browser_verify.check

    graph = _LiveGraph(session.base_url)
    # Do not clobber a Stop that landed during login / before the loop starts.
    if session.stop_event.is_set():
        session.phase = "stopped"
        session.emit({"type": "log", "level": "info", "msg": "stopping: stopped by client"})
        return session.steps
    session.phase = "exploring"
    session.emit({"type": "log", "level": "info", "msg": "exploration started"})
    # Seed from prior runs when the caller did not inject history (production
    # path). Tests pass empty dicts / explicit fixtures and skip disk.
    if not deps.known_bad and not deps.proven_tactics and deps.episode is not None:
        try:
            bad = history.known_bad(deps.episode.root, session.product_id)
            deps.known_bad = {k: count for k, (_kind, count) in bad.items()}
            deps.proven_tactics = history.proven_tactics(
                deps.episode.root, session.product_id
            )
            if deps.proven_tactics:
                session.emit(
                    {
                        "type": "log",
                        "level": "info",
                        "msg": (
                            f"history: {len(deps.proven_tactics)} proven tactic(s), "
                            f"{len(deps.known_bad)} known-bad key(s)"
                        ),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[explore] history load failed: {exc}", flush=True)
    # Starting page is already "seen" for the demo — first NEW path becomes step 1.
    start_path = urlparse(_current_url(deps.page)).path or "/"
    session.flow_paths.add(start_path)
    session.emit({"type": "status", **session.status()})
    session.publish_frame(deps.page, min_interval_s=0)

    while True:
        stop = session.budget_exhausted()
        if stop:
            session.stop_reason = stop
            session.emit({"type": "log", "level": "info", "msg": f"stopping: {stop}"})
            break

        url = _current_url(deps.page)
        if not is_product_surface(url, session.base_url):
            session.emit(
                {
                    "type": "log",
                    "level": "warn",
                    "msg": f"off-surface URL {url!r} — returning to product",
                }
            )
            if recover_product_surface(deps.page, session.base_url):
                session.publish_frame(deps.page)
                continue
            session.stop_reason = "browser left the product surface"
            session.emit(
                {"type": "log", "level": "info", "msg": f"stopping: {session.stop_reason}"}
            )
            break

        elements = perceive.inventory(deps.page)
        fp = fingerprint(url, elements)
        visited_paths = tuple(dict.fromkeys(p.url_path for p in session.visited))
        if session.mark_visited(fp):
            session.emit(
                {"type": "log", "level": "info",
                 "msg": f"new state {fp.url_path} ({len(elements)} elements)"}
            )
            session.emit(
                {
                    "type": "explored",
                    "url": url,
                    "path": fp.url_path,
                    "elements": len(elements),
                    "msg": f"opened {fp.url_path}",
                }
            )

        if not session.path_in_scope(url):
            session.emit(
                {
                    "type": "log",
                    "level": "info",
                    "msg": f"{fp.url_path} is outside the configured scope — backing out",
                }
            )
            if not _leave_out_of_scope(session, deps):
                session.stop_reason = f"out of scope at {fp.url_path}"
                session.emit(
                    {"type": "log", "level": "info",
                     "msg": f"stopping: {session.stop_reason}"}
                )
                break
            continue

        # PAGE PLAN: on a screen we have not planned yet, ask the vision model
        # for the whole demo-worthy sequence instead of one nav click at a time.
        _plan_state(session, deps, fp, elements, url)

        untried = session.untried(fp, elements)
        if not untried:
            if fp.url_path in {"blank", "/blank"} or not is_product_surface(
                url, session.base_url
            ):
                if recover_product_surface(deps.page, session.base_url):
                    session.publish_frame(deps.page)
                    continue
            # Exhausted this DOM state's untried set — do NOT tight-loop bumping
            # consecutive_no_new (that stopped runs at ~40% with pages left).
            if _try_dead_end_escape(session, deps, graph, fp, elements, url, execute, verify):
                session.emit({"type": "status", **session.status()})
                session.publish_frame(deps.page)
                continue
            session.stop_reason = f"dead end at {fp.url_path}"
            session.emit(
                {
                    "type": "log",
                    "level": "info",
                    "msg": f"stopping: {session.stop_reason}",
                }
            )
            break

        session.consecutive_no_new = 0

        choice, planned_kind = _next_from_plan(session, fp, elements, untried, url)
        if choice is None:
            planned_kind = ""
            choice = reason.choose_next(
                url=url,
                elements=untried,
                corrections=deps.corrections,
                visited_paths=visited_paths,
                known_bad=deps.known_bad or None,
                product_base=session.base_url,
                focus_hint=session.focus_hint,
                skip=lambda el: session.out_of_scope(el, url) is not None,
                ask_text=deps.ask_text,
                ask_vision=deps.ask_vision,
                screenshot=(
                    perceive.screenshot_b64(deps.page)
                    if reason.needs_vision(untried)
                    else ""
                ),
            )
        if session.stop_event.is_set():
            session.stop_reason = "stopped by client"
            session.emit({"type": "log", "level": "info", "msg": "stopping: stopped by client"})
            break
        if choice is None:
            # Reasoner / heuristic had nothing — try escape before counting a stall.
            if _try_dead_end_escape(session, deps, graph, fp, elements, url, execute, verify):
                session.emit({"type": "status", **session.status()})
                session.publish_frame(deps.page)
                continue
            session.consecutive_no_new += 1
            session.emit(
                {
                    "type": "log",
                    "level": "info",
                    "msg": "no usable next action — stalling",
                }
            )
            continue

        el = untried[choice.index]
        session.mark_tried(fp, el)
        try:
            _step(
                session, deps, graph, el, url, choice, execute, verify,
                planned_kind=planned_kind,
            )
        except RuntimeError as exc:
            if "stopped by client" in str(exc).lower():
                session.stop_reason = "stopped by client"
                session.emit(
                    {"type": "log", "level": "info", "msg": "stopping: stopped by client"}
                )
                break
            raise

        # Full status snapshot so the dashboard meter updates without waiting
        # on the HTTP poll (which previously never started when active was false).
        session.emit({"type": "status", **session.status()})
        session.publish_frame(deps.page)

    if session.stop_event.is_set():
        session.phase = "stopped"
        session.stop_reason = session.stop_reason or "stopped by client"
    return session.steps


def _plan_state(
    session: ExplorationSession,
    deps: ExplorerDeps,
    fp: Any,
    elements: list[dict[str, Any]],
    url: str,
) -> None:
    """Build (once) the demo-worthy action sequence for this screen."""
    if fp in session.page_plans:
        return
    if deps.ask_vision is None and deps.ask_text is None:
        session.page_plans[fp] = page_plan.PagePlan()
        return

    visited_paths = tuple(dict.fromkeys(p.url_path for p in session.visited))
    try:
        plan = page_plan.plan_page(
            url=url,
            elements=elements,
            screenshot_b64=perceive.screenshot_b64(deps.page),
            ask_vision=deps.ask_vision,
            ask_text=deps.ask_text,
            visited_paths=visited_paths,
            focus_hint=session.focus_hint,
            corrections=deps.corrections,
        )
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[explore] page plan failed: {exc}", flush=True)
        plan = page_plan.PagePlan()

    session.page_plans[fp] = plan
    if plan:
        session.emit(
            {
                "type": "log",
                "level": "info",
                "msg": (
                    f"planned {len(plan.actions)} action(s) on {fp.url_path}"
                    + (f" — {plan.purpose}" if plan.purpose else "")
                ),
            }
        )


def _next_from_plan(
    session: ExplorationSession,
    fp: Any,
    elements: list[dict[str, Any]],
    untried: list[dict[str, Any]],
    url: str,
) -> tuple[reason.Choice | None, str]:
    """Pop the next planned action that is still untried and in scope.

    Returns (choice indexed into `untried`, planned kind). (None, "") means the
    plan is spent or unusable and the caller should fall back to reason.
    """
    plan = session.page_plans.get(fp)
    if not plan:
        return None, ""

    untried_keys = {element_key(e): i for i, e in enumerate(untried)}
    for action in plan.actions:
        if not 0 <= action.element_index < len(elements):
            continue
        el = elements[action.element_index]
        idx = untried_keys.get(element_key(el))
        if idx is None:
            continue  # already tried this run
        if session.out_of_scope(el, url) is not None:
            session.mark_tried(fp, el)
            continue
        if not action.demo_worthy and action.kind != "commit":
            session.mark_tried(fp, el)
            continue
        return (
            reason.Choice(idx, action.why or "page plan", action.narration),
            action.kind,
        )
    return None, ""


def _leave_out_of_scope(session: ExplorationSession, deps: ExplorerDeps) -> bool:
    """Back out of a page the Client excluded. False when we cannot."""
    before = _current_url(deps.page)
    try:
        deps.page.go_back(timeout=8000)
    except Exception:  # noqa: BLE001
        return False
    return _current_url(deps.page) != before


def _try_dead_end_escape(
    session: ExplorationSession,
    deps: ExplorerDeps,
    graph: _LiveGraph,
    fp: Any,
    elements: list[dict[str, Any]],
    url: str,
    execute: Callable[..., tuple[ToolResult, str]],
    verify: Callable[..., Any],
) -> bool:
    """Leave an exhausted page: unvisited nav (even if tried) or browser back.

    Returns True if we performed an escape action and the loop should continue.
    """
    visited_paths = tuple(dict.fromkeys(p.url_path for p in session.visited))
    # Prefer nav that still claims an unvisited destination; skip keys we already
    # used for escape so a no-op click cannot spin forever.
    for i, el in enumerate(elements):
        if el.get("fillable"):
            continue
        if reason.targets_visited_path(el, visited_paths):
            continue
        if element_is_external(el, session.base_url, page_url=url):
            continue
        if not reason.looks_like_nav(el):
            continue
        ek = element_key(el)
        if ek in session.escape_attempts:
            continue
        session.escape_attempts.add(ek)
        choice = reason.Choice(i, "dead-end escape: unvisited destination", "")
        session.emit(
            {
                "type": "log",
                "level": "info",
                "msg": f"dead-end escape: retry nav toward new page ({_label(el)})",
            }
        )
        session.mark_tried(fp, el)
        try:
            _step(session, deps, graph, el, url, choice, execute, verify)
        except RuntimeError as exc:
            if "stopped by client" in str(exc).lower():
                raise
            session.emit(
                {"type": "log", "level": "warn", "msg": f"escape click failed: {exc}"}
            )
            return False
        return True

    if fp in session.dead_ends:
        return False
    session.dead_ends.add(fp)

    before = _current_url(deps.page)
    try:
        deps.page.go_back(timeout=8000)
    except Exception as exc:  # noqa: BLE001
        session.emit(
            {
                "type": "log",
                "level": "info",
                "msg": f"no untried elements at {fp.url_path}; go_back failed: {exc}",
            }
        )
        _record_escape(
            deps,
            element_key_s="go_back",
            alias="go_back",
            selector="",
            tool="navigate",
            ok=False,
            detail=str(exc),
            url_before=before,
            url_after=before,
        )
        session.consecutive_no_new += 1
        return False

    after = _current_url(deps.page)
    if not is_product_surface(after, session.base_url):
        if recover_product_surface(deps.page, session.base_url):
            session.emit(
                {
                    "type": "log",
                    "level": "info",
                    "msg": "backed up from off-surface page — recovered to product",
                }
            )
            return True
    if after == before:
        session.emit(
            {
                "type": "log",
                "level": "info",
                "msg": f"no untried elements at {fp.url_path}; nowhere to go back",
            }
        )
        _record_escape(
            deps,
            element_key_s="go_back",
            alias="go_back",
            selector="",
            tool="navigate",
            ok=False,
            detail="nowhere to go back",
            url_before=before,
            url_after=after,
        )
        session.consecutive_no_new += 1
        return False

    session.consecutive_no_new += 1
    session.emit(
        {
            "type": "log",
            "level": "info",
            "msg": f"backed up from exhausted page {fp.url_path}",
        }
    )
    _record_escape(
        deps,
        element_key_s="go_back",
        alias="go_back",
        selector="",
        tool="navigate",
        ok=True,
        detail=f"backed up from {fp.url_path}",
        url_before=before,
        url_after=after,
    )
    return True


def _record_escape(
    deps: ExplorerDeps,
    *,
    element_key_s: str,
    alias: str,
    selector: str,
    tool: str,
    ok: bool,
    detail: str,
    url_before: str,
    url_after: str,
) -> None:
    if deps.episode is None:
        return
    deps.episode.record(
        StepAttempt(
            element_key=element_key_s,
            alias=alias,
            selector=selector,
            tool=tool,
            attempt=0,
            tactic="dead_end_escape",
            kind="" if ok else "unknown",
            ok=ok,
            detail=detail,
            duration_ms=0,
            url_before=url_before,
            url_after=url_after,
        )
    )


def _step(
    session: ExplorationSession,
    deps: ExplorerDeps,
    graph: _LiveGraph,
    el: dict[str, Any],
    url: str,
    choice: reason.Choice,
    execute: Callable[..., tuple[ToolResult, str]],
    verify: Callable[..., Any],
    planned_kind: str = "",
) -> None:
    if session.stop_event.is_set():
        return

    alias, css = prefer_selector(el)
    junk = junk_record_reason(el, alias=alias, selector=css)
    if junk:
        session.emit({"type": "log", "level": "debug", "msg": f"skip {alias}: {junk}"})
        return

    scope = session.out_of_scope(el, url)
    if scope:
        session.emit(
            {"type": "log", "level": "info", "msg": f"skip {alias}: {scope}"}
        )
        return

    fillable = perceive.is_fillable(el)
    ek = element_key(el)

    ext_reason = element_is_external(el, session.base_url, page_url=url)
    if ext_reason and not fillable:
        session.emit(
            {
                "type": "log",
                "level": "info",
                "msg": f"skip external link {_label(el)!r} — {ext_reason}",
            }
        )
        return

    # GUARDRAIL. Runs here, in the executor path, on the element actually about
    # to be touched -- deliberately not inside the reasoning prompt. A reasoning
    # step that suggests a destructive action cannot get past this point.
    # Client "Allow" on a flagged item adds to allowed_keys for this run only.
    if session.is_allowed(el, css):
        pass  # Client already approved — proceed to act.
    else:
        verdict = classify_action(el, judge=deps.guard_judge)
        if session.stop_event.is_set():
            return
        if verdict.flagged:
            flag = FlaggedAction(
                label=_label(el),
                selector=css,
                url=url,
                reason=verdict.reason,
                source=verdict.source,
                element_key=ek,
            )
            if session.note_flagged(flag):
                session.emit({"type": "flagged", **flag.as_dict()})
            # A form the plan wanted to submit: the fills before it are real
            # demo material, so record the commit as a step the Client can
            # approve. It is NOT executed here -- no live data is mutated.
            if planned_kind == "commit" and not fillable:
                _record_pending_commit(session, graph, el, alias, css, url, verdict.reason)
            return

    if session.stop_event.is_set():
        return

    graph.add(alias, css)
    field_value: str | None = None

    if fillable:
        field_value = _resolve_field_value(session, deps, el, alias)
        if field_value is None:
            return
        call: Any = FillField(
            selector=alias, value=field_value,
            expects=Postcondition(check="value_equals", selector=alias, expected=field_value),
        )
        step = RecordedStep(tool="fill_field", alias=alias, selector=css, value=field_value)
    else:
        expects = click_postcondition(alias, el)
        call = ClickElement(selector=alias, expects=expects)
        step = RecordedStep(tool="click_element", alias=alias, selector=css)

    step.postcondition = guess_postcondition(step)
    url_before = url
    elements_before = perceive.inventory(deps.page)
    fp_before = fingerprint(url_before, elements_before)
    snap_before = (
        semantics.state_snapshot(deps.page)
        if deps.label_ask is not None
        else semantics.StateSnapshot()
    )

    started = time.perf_counter()
    result, _next_page = execute(deps.page, graph, EXPLORE_PAGE_ID, call)
    duration_ms = int((time.perf_counter() - started) * 1000)

    verify_result = None
    if result.ok:
        try:
            verify_result = verify(deps.page, graph, EXPLORE_PAGE_ID, call.expects)
        except Exception as exc:  # noqa: BLE001
            session.emit({"type": "log", "level": "warn", "msg": f"verify error: {exc}"})

    url_after = _current_url(deps.page)
    if is_external_url(url_after, session.base_url):
        revert_external_navigation(deps.page, product_base=session.base_url)
        session.emit(
            {
                "type": "log",
                "level": "info",
                "msg": f"left off-product page after {_label(el)!r} — not shown in Watch bot",
            }
        )
        return

    elements_after = perceive.inventory(deps.page)
    fp_after = fingerprint(url_after, elements_after)

    stalled = looks_nav_stalled(
        fillable=fillable,
        result_ok=result.ok,
        url_before=url_before,
        url_after=url_after,
        fp_before=fp_before,
        fp_after=fp_after,
    ) and (
        bool(el.get("href"))
        or reason.looks_like_nav(el)
    )
    verify_ok = click_verify_passed(result, verify_result, call.expects)
    path_before = urlparse(url_before).path or "/"
    path_after = urlparse(url_after).path or "/"
    navigated = not fillable and result.ok and path_before != path_after
    if navigated and not stalled and not verify_ok:
        # Nav succeeded but visibility verify flaky (tabs, SPA shells) — still
        # count as a demo step when we landed on a new path.
        verify_ok = True
    passed = bool(result.ok and verify_ok and not stalled)

    kind = ""
    if not passed:
        kind = classify(
            result,
            verify_passed=False if verify_result is not None and not verify_result.passed else None,
            verify_actual=getattr(verify_result, "actual", "") if verify_result else "",
            nav_stalled=stalled,
        )

    _record_attempt(
        deps,
        ek=ek,
        alias=alias,
        selector=css,
        tool=step.tool,
        attempt=0,
        tactic="",
        kind=kind,
        ok=passed,
        detail=result.detail if not result.ok else (
            getattr(verify_result, "actual", "") if verify_result and not verify_ok
            else ("nav_stalled" if stalled else result.detail)
        ),
        duration_ms=duration_ms,
        url_before=url_before,
        url_after=url_after,
    )

    session.actions_taken += 1

    if not passed:
        remaining = session.budget.max_repairs_total - session.repairs_used
        per_step = min(session.budget.max_repairs_per_step, max(0, remaining))
        if per_step > 0 and kind not in ("disabled", "unknown"):
            session.emit(
                {
                    "type": "repair",
                    "kind": kind,
                    "alias": alias,
                    "element_key": ek,
                    "msg": f"repairing {alias} ({kind})",
                }
            )
            path_now = urlparse(url_before).path or "/"
            rctx = RepairCtx(
                page=deps.page,
                graph=graph,
                page_id=EXPLORE_PAGE_ID,
                el=el,
                alias=alias,
                css=css,
                fillable=fillable,
                value=field_value,
                execute=execute,
                verify=verify,
                guard_judge=deps.guard_judge,
                is_allowed=session.is_allowed,
                max_repairs=per_step,
                inventory=perceive.inventory,
                proven_tactic=deps.proven_tactics.get((path_now, kind or "")),
                ask_vision=deps.ask_vision,
                vlm_locates_left=max(
                    0, session.budget.max_vlm_locates_per_run - session.vlm_locates_used
                ),
                on_vlm_locate=lambda: setattr(
                    session, "vlm_locates_used", session.vlm_locates_used + 1
                ),
            )
            outcome = run_ladder(rctx, kind)  # type: ignore[arg-type]
            for i, att in enumerate(outcome.attempts, start=1):
                session.repairs_used += 1
                _record_attempt(
                    deps,
                    ek=ek,
                    alias=att.alias,
                    selector=att.css,
                    tool=step.tool,
                    attempt=i,
                    tactic=att.tactic,
                    kind="" if att.ok else kind,
                    ok=att.ok,
                    detail=att.result.detail if att.result else att.tactic,
                    duration_ms=att.result.duration_ms if att.result else 0,
                    url_before=url_before,
                    url_after=_current_url(deps.page),
                )
            if outcome.ok and outcome.result is not None:
                passed = True
                result = outcome.result
                verify_result = outcome.verify_result
                alias = outcome.alias or alias
                css = outcome.css or css
                step.alias = alias
                step.selector = css
                session.emit(
                    {
                        "type": "repair",
                        "kind": kind,
                        "alias": alias,
                        "ok": True,
                        "tactics": outcome.tactics_tried,
                        "msg": f"repaired {alias} via {outcome.tactics_tried}",
                    }
                )
            else:
                session.emit(
                    {
                        "type": "log",
                        "level": "warn",
                        "msg": f"{step.tool} {alias} failed: {kind} — repairs exhausted",
                    }
                )
                _maybe_shot(deps)
        else:
            detail = (
                result.detail if not result.ok
                else getattr(verify_result, "actual", "") if verify_result else kind
            )
            session.emit(
                {"type": "log", "level": "warn", "msg": f"{step.tool} {alias} failed: {detail}"}
            )
            _maybe_shot(deps)

    if passed:
        after_path = urlparse(_current_url(deps.page)).path or "/"
        if fillable and step.tool == "fill_field":
            _mark_live_input_step(step, session, el)
            session.steps.append(step)
            session.consecutive_no_new = 0
            _label_step(
                session,
                deps,
                tool=step.tool,
                element=_label(el),
                snap_before=snap_before,
                elements_before=elements_before,
            )
            session.emit(
                {
                    "type": "log",
                    "level": "info",
                    "msg": (
                        f"demo step +{alias} (fill on {after_path}) — "
                        f"{choice.why or 'ok'}"
                    ),
                }
            )
        elif after_path not in session.flow_paths or (
            path_before == after_path and fp_before != fp_after
        ):
            # A new path, OR the same path with a changed DOM -- a dialog opened,
            # a row expanded, a filter applied. The old path-only rule discarded
            # every in-page interaction, which is what made demos a tab tour.
            # Landing back on an already-covered path stays excluded: that is a
            # backtrack, not a new thing to show.
            new_path = after_path not in session.flow_paths
            session.flow_paths.add(after_path)
            session.steps.append(step)
            session.consecutive_no_new = 0
            _label_step(
                session,
                deps,
                tool=step.tool,
                element=_label(el),
                snap_before=snap_before,
                elements_before=elements_before,
            )
            session.emit(
                {
                    "type": "log",
                    "level": "info",
                    "msg": (
                        f"demo step +{alias} "
                        f"{'→ ' + after_path if new_path else f'(in-page on {after_path})'}"
                        f" — {choice.why or 'ok'}"
                    ),
                }
            )
        else:
            session.consecutive_no_new += 1
            session.emit(
                {
                    "type": "log",
                    "level": "info",
                    "msg": (
                        f"explored {alias} on {url} → {after_path} "
                        f"(already in demo — not added)"
                    ),
                }
            )

    if deps.on_action is not None:
        deps.on_action(step, result, verify_result)


def _record_pending_commit(
    session: ExplorationSession,
    graph: _LiveGraph,
    el: dict[str, Any],
    alias: str,
    css: str,
    url: str,
    reason_text: str,
) -> None:
    """Save the mutating click as an unapproved step. Never executes it."""
    graph.add(alias, css)
    step = RecordedStep(
        tool="click_element",
        alias=alias,
        selector=css,
        needs_approval=True,
        approval_reason=reason_text,
    )
    step.postcondition = guess_postcondition(step)
    session.steps.append(step)
    session.step_labels.append(
        f"Completes the form by choosing {_label(el)!r}."
    )
    session.note_pending_approval(
        alias=alias, label=_label(el), selector=css, url=url, reason=reason_text
    )
    session.emit(
        {
            "type": "log",
            "level": "info",
            "msg": (
                f"recorded (not run) {alias} — needs approval: {reason_text}"
            ),
        }
    )


def _mark_live_input_step(
    step: RecordedStep,
    session: ExplorationSession,
    el: dict[str, Any],
) -> None:
    """Business-specific fills become live-input beats in the saved demo."""
    for dec in reversed(session.field_decisions):
        if dec.alias != step.alias:
            continue
        if dec.classification != "business_specific":
            return
        step.source = "user"
        step.live_question = question_for(el)
        if dec.value:
            step.value = dec.value
        return


def _label_step(
    session: ExplorationSession,
    deps: ExplorerDeps,
    *,
    tool: str,
    element: str,
    snap_before: semantics.StateSnapshot,
    elements_before: list[dict[str, Any]],
) -> None:
    """Describe what the step just accomplished, into `session.step_labels`.

    Parallel to `session.steps` by index, so a missing label is an empty string
    rather than a hole. Re-perceives after the fact because a repair may have
    changed the page between the original attempt and this point.
    """
    if deps.label_ask is None:
        session.step_labels.append("")
        return

    snap_after = semantics.state_snapshot(deps.page)
    diff = semantics.diff_summary(
        snap_before, snap_after, elements_before, perceive.inventory(deps.page)
    )
    session.step_labels.append(
        semantics.label_step(
            tool=tool,
            element=element,
            before=snap_before,
            after=snap_after,
            diff=diff,
            ask_text=deps.label_ask,
        )
    )


def _record_attempt(
    deps: ExplorerDeps,
    *,
    ek: str,
    alias: str,
    selector: str,
    tool: str,
    attempt: int,
    tactic: str,
    kind: str,
    ok: bool,
    detail: str,
    duration_ms: int,
    url_before: str,
    url_after: str,
) -> None:
    if deps.episode is None:
        return
    deps.episode.record(
        StepAttempt(
            element_key=ek,
            alias=alias,
            selector=selector,
            tool=tool,
            attempt=attempt,
            tactic=tactic,
            kind=kind,
            ok=ok,
            detail=detail or "",
            duration_ms=duration_ms,
            url_before=url_before,
            url_after=url_after,
        )
    )


def _maybe_shot(deps: ExplorerDeps) -> None:
    """Screenshot unrepaired failures only (capped inside EpisodeStore)."""
    if deps.episode is None:
        return
    b64 = perceive.screenshot_b64(deps.page, image_type="jpeg", quality=40)
    if not b64:
        return
    try:
        deps.episode.save_shot(base64.b64decode(b64))
    except Exception:  # noqa: BLE001
        pass


def _resolve_field_value(
    session: ExplorationSession,
    deps: ExplorerDeps,
    el: dict[str, Any],
    alias: str,
) -> str | None:
    """Placeholder for a guessable field, or the client's answer. None = skip."""
    plan = classify_field(el, judge=deps.field_judge)
    label = _label(el)

    if plan.classification == "guessable_safe":
        session.field_decisions.append(
            FieldDecision(alias, label, "guessable_safe", plan.value, "auto")
        )
        session.emit(
            {"type": "field", "alias": alias, "classification": "guessable_safe",
             "value": plan.value}
        )
        return plan.value

    session.publish_frame(deps.page, min_interval_s=0)
    question = session.ask(
        alias,
        question_for(el),
        {"url": _current_url(deps.page), "label": label,
         "input_type": el.get("type") or "text", "reason": plan.reason},
    )
    if question.skipped or not question.answer:
        session.field_decisions.append(
            FieldDecision(
                alias, label, "business_specific", "",
                "skipped_timeout" if question.timed_out else "skipped_client",
            )
        )
        return None

    session.field_decisions.append(
        FieldDecision(alias, label, "business_specific", question.answer, "client")
    )
    session.emit(
        {"type": "field", "alias": alias, "classification": "business_specific",
         "value": question.answer}
    )
    return question.answer


def _label(el: dict[str, Any]) -> str:
    return str(
        el.get("label") or el.get("text") or el.get("aria_label")
        or el.get("title") or el.get("name") or el.get("testid") or el.get("tag") or "element"
    )


def _current_url(page: Any) -> str:
    try:
        return page.url or ""
    except Exception:  # noqa: BLE001
        return ""
