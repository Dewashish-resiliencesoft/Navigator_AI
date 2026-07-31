"""EXECUTING: run one tool call from the plan.

One call per pass, not the whole plan, so every call gets its own VERIFYING pass
before the next one runs. A failed postcondition should stop the agent from
building on a broken assumption.
"""

from __future__ import annotations

from navigator.agent.state import CallDeps, CallState
from navigator.browser.tools import execute as run_tool


def executing(state: CallState, deps: CallDeps) -> CallState:
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
