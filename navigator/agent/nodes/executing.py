"""EXECUTING: run one tool call from the plan.

One call per pass, not the whole plan, so every call gets its own VERIFYING pass
before the next one runs. A failed postcondition should stop the agent from
building on a broken assumption.
"""

from __future__ import annotations

from navigator.agent.state import CallDeps, CallState
from navigator.automation.browser.tools import execute as run_tool


def executing(state: CallState, deps: CallDeps) -> CallState:
    label = state.get("nav_click_label")
    if label:
        from navigator.automation.browser.nav_click import click_nav_label
        from navigator.core.schemas import ToolResult

        try:
            click_nav_label(deps.page, label)
            detail = f"clicked nav {label!r}"
            ok = True
        except Exception as exc:  # noqa: BLE001
            detail = str(exc).splitlines()[0] if str(exc) else "nav click failed"
            ok = False
        if deps.push_frame is not None:
            deps.push_frame()
        return CallState(
            nav_click_label=None,
            pending_calls=list(state.get("pending_calls") or []),
            last_call=None,
            last_result=ToolResult(
                ok=ok, tool="click_element", detail=detail, duration_ms=0
            ),
            last_page_id=state.get("page_id") or "",
        )

    pending = list(state["pending_calls"])
    if not pending:
        return CallState(last_call=None, last_result=None)

    call, rest = pending[0], pending[1:]
    ran_on = state["page_id"]
    result, next_page_id = run_tool(deps.page, deps.graph, ran_on, call)
    if deps.push_frame is not None:
        deps.push_frame()

    return CallState(
        pending_calls=rest,
        last_call=call,
        last_result=result,
        last_page_id=ran_on,
        page_id=next_page_id,
    )
