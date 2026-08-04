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

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import uuid4

from navigator.automation.explore import perceive, reason
from navigator.automation.explore.fields import classify_field, question_for
from navigator.automation.explore.guardrail import FlaggedAction, classify_action
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
    # Starting page is already "seen" for the demo — first NEW path becomes step 1.
    start_path = urlparse(_current_url(deps.page)).path or "/"
    session.flow_paths.add(start_path)
    session.emit({"type": "status", **session.status()})

    while True:
        stop = session.budget_exhausted()
        if stop:
            session.emit({"type": "log", "level": "info", "msg": f"stopping: {stop}"})
            break

        url = _current_url(deps.page)
        elements = perceive.inventory(deps.page)
        fp = fingerprint(url, elements)
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

        untried = session.untried(fp, elements)
        if not untried:
            session.consecutive_no_new += 1
            session.emit(
                {"type": "log", "level": "info",
                 "msg": f"no untried elements at {fp.url_path}"}
            )
            continue
        session.consecutive_no_new = 0

        choice = reason.choose_next(
            url=url,
            elements=untried,
            corrections=deps.corrections,
            visited_paths=tuple(dict.fromkeys(fp.url_path for fp in session.visited)),
            ask_text=deps.ask_text,
            ask_vision=deps.ask_vision,
            screenshot=(
                perceive.screenshot_b64(deps.page)
                if reason.needs_vision(untried)
                else ""
            ),
        )
        if session.stop_event.is_set():
            session.emit({"type": "log", "level": "info", "msg": "stopping: stopped by client"})
            break
        if choice is None:
            session.consecutive_no_new += 1
            continue

        el = untried[choice.index]
        session.mark_tried(fp, el)
        try:
            _step(session, deps, graph, el, url, choice, execute, verify)
        except RuntimeError as exc:
            if "stopped by client" in str(exc).lower():
                session.emit(
                    {"type": "log", "level": "info", "msg": "stopping: stopped by client"}
                )
                break
            raise

        # Full status snapshot so the dashboard meter updates without waiting
        # on the HTTP poll (which previously never started when active was false).
        session.emit({"type": "status", **session.status()})

    if session.stop_event.is_set():
        session.phase = "stopped"
    return session.steps


def _step(
    session: ExplorationSession,
    deps: ExplorerDeps,
    graph: _LiveGraph,
    el: dict[str, Any],
    url: str,
    choice: reason.Choice,
    execute: Callable[..., tuple[ToolResult, str]],
    verify: Callable[..., Any],
) -> None:
    if session.stop_event.is_set():
        return

    alias, css = prefer_selector(el)
    junk = junk_record_reason(el, alias=alias, selector=css)
    if junk:
        session.emit({"type": "log", "level": "debug", "msg": f"skip {alias}: {junk}"})
        return

    fillable = perceive.is_fillable(el)

    # GUARDRAIL. Runs here, in the executor path, on the element actually about
    # to be touched -- deliberately not inside the reasoning prompt. A reasoning
    # step that suggests a destructive action cannot get past this point.
    verdict = classify_action(el, judge=deps.guard_judge)
    if session.stop_event.is_set():
        return
    if verdict.flagged:
        flag = FlaggedAction(
            label=_label(el), selector=css, url=url,
            reason=verdict.reason, source=verdict.source,
        )
        session.flagged.append(flag)
        session.emit({"type": "flagged", **flag.as_dict()})
        return

    graph.add(alias, css)

    if fillable:
        value = _resolve_field_value(session, deps, el, alias)
        if value is None:
            return
        call: Any = FillField(
            selector=alias, value=value,
            expects=Postcondition(check="value_equals", selector=alias, expected=value),
        )
        step = RecordedStep(tool="fill_field", alias=alias, selector=css, value=value)
    else:
        call = ClickElement(
            selector=alias, expects=Postcondition(check="visible", selector=alias)
        )
        step = RecordedStep(tool="click_element", alias=alias, selector=css)

    step.postcondition = guess_postcondition(step)
    result, _next_page = execute(deps.page, graph, EXPLORE_PAGE_ID, call)

    verify_result = None
    if result.ok:
        try:
            verify_result = verify(deps.page, graph, EXPLORE_PAGE_ID, call.expects)
        except Exception as exc:  # noqa: BLE001
            session.emit({"type": "log", "level": "warn", "msg": f"verify error: {exc}"})

    session.actions_taken += 1
    passed = bool(result.ok and (verify_result is None or verify_result.passed))
    if passed:
        after_path = urlparse(_current_url(deps.page)).path or "/"
        # Demo flow = first landing on each URL path only. Revisits / backtracks
        # still explore the site but do not clutter the walkthrough.
        if after_path not in session.flow_paths:
            session.flow_paths.add(after_path)
            session.steps.append(step)
            session.consecutive_no_new = 0
            session.emit(
                {
                    "type": "log",
                    "level": "info",
                    "msg": (
                        f"demo step +{alias} → {after_path} — "
                        f"{choice.why or 'ok'}"
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
    else:
        detail = result.detail if not result.ok else getattr(verify_result, "actual", "")
        session.emit(
            {"type": "log", "level": "warn", "msg": f"{step.tool} {alias} failed: {detail}"}
        )

    if deps.on_action is not None:
        deps.on_action(step, result, verify_result)


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
